import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError, WaiterError

from .config import Config
from .log import log


class Stack:
    def __init__(
        self,
        tier: str,
        name: str,
        template: str,
        parameters: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
        template_root: "Path | None" = None,
    ):
        self.tier = tier
        self.name = name
        self.template_relative = template
        self.parameters = parameters or {}
        self.capabilities = capabilities or ["CAPABILITY_NAMED_IAM"]
        self.full_name = Config.stack_name(tier, name)
        self.template_path = (template_root / template) if template_root else Config.template_path(template)
        self._cf = None

    @property
    def cf(self):
        if self._cf is None:
            self._cf = boto3.client("cloudformation", region_name=Config.REGION)
        return self._cf

    def status(self) -> str | None:
        try:
            r = self.cf.describe_stacks(StackName=self.full_name)
            return r["Stacks"][0]["StackStatus"]
        except ClientError as e:
            if "does not exist" in str(e):
                return None
            raise

    def exists(self) -> bool:
        s = self.status()
        return s is not None and s != "DELETE_COMPLETE"

    def outputs(self) -> dict[str, str]:
        try:
            r = self.cf.describe_stacks(StackName=self.full_name)
            return {o["OutputKey"]: o["OutputValue"] for o in r["Stacks"][0].get("Outputs", [])}
        except ClientError:
            return {}

    def deploy(self) -> None:
        if not self.template_path.exists():
            raise FileNotFoundError(f"Template not found: {self.template_path}")

        template_body = self.template_path.read_text(encoding="utf-8")

        current = self.status()
        if current == "ROLLBACK_COMPLETE":
            log.warn(f"{self.full_name} is in ROLLBACK_COMPLETE — deleting before re-create")
            self.cf.delete_stack(StackName=self.full_name)
            self.cf.get_waiter("stack_delete_complete").wait(StackName=self.full_name)
            current = None

        change_set_type = "UPDATE" if current else "CREATE"
        cs_name = f"deploy-{int(time.time() * 1000)}"

        params = [{"ParameterKey": k, "ParameterValue": str(v), "UsePreviousValue": False}
                  for k, v in self.parameters.items()]

        log.step(f"Deploy {self.full_name}  ←  {self.template_relative}  ({change_set_type})")

        kwargs: dict[str, Any] = {
            "StackName": self.full_name,
            "TemplateBody": template_body,
            "ChangeSetName": cs_name,
            "ChangeSetType": change_set_type,
            "Capabilities": self.capabilities,
            "Parameters": params,
        }
        self.cf.create_change_set(**kwargs)

        try:
            self.cf.get_waiter("change_set_create_complete").wait(
                StackName=self.full_name, ChangeSetName=cs_name,
                WaiterConfig={"Delay": 5, "MaxAttempts": 60},
            )
        except WaiterError:
            cs = self.cf.describe_change_set(StackName=self.full_name, ChangeSetName=cs_name)
            reason = cs.get("StatusReason", "")
            if "didn't contain changes" in reason or "No updates" in reason:
                log.info(f"  no changes")
                self.cf.delete_change_set(StackName=self.full_name, ChangeSetName=cs_name)
                if change_set_type == "CREATE":
                    self.cf.delete_stack(StackName=self.full_name)
                return
            log.err(f"  change set failed: {reason}")
            raise

        self.cf.execute_change_set(StackName=self.full_name, ChangeSetName=cs_name)

        waiter_name = "stack_create_complete" if change_set_type == "CREATE" else "stack_update_complete"
        try:
            self.cf.get_waiter(waiter_name).wait(
                StackName=self.full_name,
                WaiterConfig={"Delay": 15, "MaxAttempts": 240},
            )
        except WaiterError as e:
            final = self.status()
            log.err(f"  deploy failed: {self.full_name} → {final}")
            self._print_recent_failures()
            raise
        log.success(f"  {self.full_name} → {self.status()}")

    def destroy(self) -> None:
        s = self.status()
        if s is None:
            log.info(f"{self.full_name} not found · skip")
            return
        log.step(f"Destroy {self.full_name}  (current: {s})")
        self.cf.delete_stack(StackName=self.full_name)
        try:
            self.cf.get_waiter("stack_delete_complete").wait(
                StackName=self.full_name,
                WaiterConfig={"Delay": 15, "MaxAttempts": 240},
            )
            log.success(f"  {self.full_name} deleted")
        except WaiterError:
            log.err(f"  {self.full_name} delete failed → {self.status()}")
            self._print_recent_failures()
            raise

    def _print_recent_failures(self) -> None:
        try:
            events = self.cf.describe_stack_events(StackName=self.full_name)["StackEvents"]
            failures = [e for e in events if "FAILED" in e.get("ResourceStatus", "")][:5]
            for e in failures:
                log.warn(f"    {e['LogicalResourceId']} {e['ResourceStatus']}: {e.get('ResourceStatusReason', '')[:200]}")
        except ClientError:
            pass


def list_bookflow_stacks(*, exclude_tiers: list[str] | None = None) -> list[dict]:
    cf = boto3.client("cloudformation", region_name=Config.REGION)
    paginator = cf.get_paginator("list_stacks")
    active_states = [
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
        "ROLLBACK_COMPLETE", "DELETE_FAILED", "IMPORT_COMPLETE",
    ]
    out = []
    for page in paginator.paginate(StackStatusFilter=active_states):
        for s in page["StackSummaries"]:
            if not s["StackName"].startswith(f"{Config.STACK_PREFIX}-"):
                continue
            if exclude_tiers:
                tier = s["StackName"].split("-")[1]
                if tier in exclude_tiers:
                    continue
            out.append(s)
    return out
