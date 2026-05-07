"""cicd-eks · CodePipeline + CodeBuild for BookFlowAI-Apps eks-pods

Stack : bookflow-cicd-eks
Template:  cicd/codepipeline/eks-pipeline.yaml

Deploy :
  1. eks-pipeline stack create
  2. CodeBuildRoleArn  → eks-cluster stack  update-stack   (Access Entry )

 :
  - Tier 00 codestar-connection · ecr · iam ( deploy)
  - Tier 30 eks-cluster ( ACTIVE )
  - BookFlowAI-Apps repo  main  eks-pods/  buildspec.yml 

: 🟡   deploy
"""
import boto3

from ..lib import Stack, log
from ..lib.config import Config

CICD_ROOT = Config.REPO_ROOT / "cicd" / "codepipeline"


def deploy() -> None:
    log.step("=== cicd-eks · CodePipeline + CodeBuild deploy ===")

    # 1. CICD stack
    cicd_stack = Stack(
        tier="cicd",
        name="eks",
        template="eks-pipeline.yaml",
        template_root=CICD_ROOT,
    )
    cicd_stack.deploy()

    # 2. CodeBuild role ARN  → eks-cluster  
    out = cicd_stack.outputs()
    cb_role_arn = out.get("CodeBuildRoleArn")
    if not cb_role_arn:
        log.warn("CodeBuildRoleArn output  · Access Entry   ")
        return

    log.info(f"CodeBuildRoleArn: {cb_role_arn}")

    # 3. eks-cluster stack  update-stack (CiCdRoleArn  → AccessEntry )
    cf = boto3.client("cloudformation", region_name=Config.REGION)
    cluster_stack_name = Config.stack_name("30", "eks-cluster")

    try:
        existing = cf.describe_stacks(StackName=cluster_stack_name)
        existing_params = existing["Stacks"][0].get("Parameters", [])
        current_role = next((p["ParameterValue"] for p in existing_params
                            if p["ParameterKey"] == "CiCdRoleArn"), "")
        if current_role == cb_role_arn:
            log.info(f"  eks-cluster CiCdRoleArn already up-to-date · skip")
            return
    except cf.exceptions.ClientError as e:
        if "does not exist" in str(e):
            log.warn(f"  {cluster_stack_name}  · Access Entry  skip (cluster deploy  )")
            return
        raise

    log.step(f"Update {cluster_stack_name} · inject CiCdRoleArn")
    cf.update_stack(
        StackName=cluster_stack_name,
        UsePreviousTemplate=True,
        Parameters=[
            {"ParameterKey": "CiCdRoleArn", "ParameterValue": cb_role_arn,
             "UsePreviousValue": False},
            #   parameter   
            *[{"ParameterKey": p["ParameterKey"], "UsePreviousValue": True}
              for p in existing_params if p["ParameterKey"] != "CiCdRoleArn"],
        ],
        Capabilities=["CAPABILITY_NAMED_IAM"],
    )
    cf.get_waiter("stack_update_complete").wait(
        StackName=cluster_stack_name,
        WaiterConfig={"Delay": 15, "MaxAttempts": 60},
    )
    log.success(f"  {cluster_stack_name} CiCdRoleArn injected (Access Entry active)")

    log.step("=== cicd-eks deploy  ===")


def destroy() -> None:
    log.step("=== cicd-eks destroy ===")

    # 1. eks-cluster  CiCdRoleArn  (Access Entry  · stack  skip)
    cf = boto3.client("cloudformation", region_name=Config.REGION)
    cluster_stack_name = Config.stack_name("30", "eks-cluster")
    try:
        existing = cf.describe_stacks(StackName=cluster_stack_name)
        existing_params = existing["Stacks"][0].get("Parameters", [])
        current_role = next((p["ParameterValue"] for p in existing_params
                            if p["ParameterKey"] == "CiCdRoleArn"), "")
        if current_role:
            log.step(f"Update {cluster_stack_name} · clear CiCdRoleArn (Access Entry off)")
            cf.update_stack(
                StackName=cluster_stack_name,
                UsePreviousTemplate=True,
                Parameters=[
                    {"ParameterKey": "CiCdRoleArn", "ParameterValue": "",
                     "UsePreviousValue": False},
                    *[{"ParameterKey": p["ParameterKey"], "UsePreviousValue": True}
                      for p in existing_params if p["ParameterKey"] != "CiCdRoleArn"],
                ],
                Capabilities=["CAPABILITY_NAMED_IAM"],
            )
            cf.get_waiter("stack_update_complete").wait(
                StackName=cluster_stack_name,
                WaiterConfig={"Delay": 15, "MaxAttempts": 60},
            )
    except cf.exceptions.ClientError as e:
        if "does not exist" in str(e):
            log.info(f"  {cluster_stack_name}  · cluster cleanup skip")
        else:
            raise

    # 2. CICD stack 
    Stack(tier="cicd", name="eks", template="").destroy()

    log.step("=== cicd-eks destroy  ===")
