"""task-mocks · CSP mocks helm chart on EKS (azure-entra · azure-logic-apps · gcp-vertex · gcp-bigquery).

Source chart lives in BookFlowAI-Apps repo: mocks/charts/csp-mocks/.
Set BOOKFLOW_APPS_DIR env var to that repo's root, or default to ../BookFlowAI-Apps.

CI/CD note: same task wraps `helm upgrade --install` / `helm uninstall`, so daily
start-day/stop-day scripts (or CodePipeline/GHA) call this without touching helm CLI directly.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..lib import Config, log

NAMESPACE = "stubs"
RELEASE = "csp-mocks"
DEFAULT_APPS_DIR_REL = "../BookFlowAI-Apps"


def _chart_path() -> Path:
    apps_env = os.environ.get("BOOKFLOW_APPS_DIR")
    if apps_env:
        apps = Path(apps_env).expanduser()
    else:
        # default: sibling of Platform repo
        platform_root = Path(__file__).resolve().parents[3]
        apps = platform_root.parent / "BookFlowAI-Apps"
    chart = apps / "mocks" / "charts" / "csp-mocks"
    if not chart.exists():
        log.err(f"chart not found: {chart}")
        log.err("set BOOKFLOW_APPS_DIR or place BookFlowAI-Apps next to BookFlowAI-Platform")
        raise SystemExit(1)
    return chart


def _ensure_kubeconfig() -> None:
    cluster = "bookflow-eks"
    log.info(f"updating kubeconfig for {cluster} ({Config.REGION})")
    cmd = [
        "aws", "eks", "update-kubeconfig",
        "--name", cluster,
        "--region", Config.REGION,
    ]
    subprocess.run(cmd, check=True)


def _ensure_tools() -> None:
    for tool in ("helm", "kubectl", "aws"):
        if shutil.which(tool) is None:
            log.err(f"required tool not on PATH: {tool}")
            raise SystemExit(1)


def deploy() -> None:
    log.step(f"=== task-mocks · helm upgrade --install {RELEASE} ===")
    _ensure_tools()
    _ensure_kubeconfig()
    chart = _chart_path()
    account_id = Config.account_id()
    ecr_registry = f"{account_id}.dkr.ecr.{Config.REGION}.amazonaws.com"
    image_tag = os.environ.get("MOCKS_IMAGE_TAG", "latest")

    cmd = [
        "helm", "upgrade", "--install", RELEASE, str(chart),
        "--create-namespace",
        "--namespace", NAMESPACE,
        "--set", f"ecrRegistry={ecr_registry}",
        "--set", f"imageTag={image_tag}",
        "--wait", "--timeout", "5m",
    ]
    log.info("$ " + " ".join(cmd))
    subprocess.run(cmd, check=True)

    log.step(f"=== task-mocks complete · pods in ns/{NAMESPACE} ===")
    subprocess.run(
        ["kubectl", "get", "pods", "-n", NAMESPACE, "-o", "wide"],
        check=False,
    )


def destroy() -> None:
    log.step(f"=== task-mocks-down · helm uninstall {RELEASE} + drop ns/{NAMESPACE} ===")
    _ensure_tools()
    _ensure_kubeconfig()

    # uninstall release (idempotent: ignore missing)
    subprocess.run(
        ["helm", "uninstall", RELEASE, "--namespace", NAMESPACE, "--ignore-not-found"],
        check=False,
    )
    # drop namespace (cleanup any dangling Service/PVC)
    subprocess.run(
        ["kubectl", "delete", "namespace", NAMESPACE, "--ignore-not-found=true"],
        check=False,
    )
    log.step("=== task-mocks-down complete ===")
