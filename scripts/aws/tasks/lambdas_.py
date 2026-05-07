"""task-lambdas · Tier 99-serverless (7 Lambdas + EventBridge + Kinesis ESM + API Gateway).

SAM template auto-fetches RDS/Redis/SF/Secret/Bucket params from existing stacks.
Falls back to master-password ARN when per-pod secrets (pos_ingestor/spike_detect) absent.
"""
import boto3

from ..lib import Stack, log
from ..lib.config import Config


def deploy() -> None:
    log.step("=== task-lambdas · 7 Lambdas SAM ===")
    if not Stack(tier="10", name="vpc-bookflow-ai", template="").exists():
        log.err("vpc-bookflow-ai "); raise SystemExit(1)
    if not Stack(tier="20", name="kinesis", template="").exists():
        log.err("kinesis  · task-data "); raise SystemExit(1)
    rds_stack = Stack(tier="20", name="rds", template="")
    if not rds_stack.exists():
        log.warn("RDS  · pos-ingestor / spike-detect  ")
    if not Stack(tier="10", name="peering-bookflow-ai-data", template="").exists():
        log.warn("peering bookflow-ai-data  · pos-ingestor → RDS ")

    params = {
        "LambdaArtifactBucket": f"{Config.PROJECT_NAME}-cp-artifacts-{Config.account_id()}",
    }

    # RDS endpoint (optional but pos-ingestor/spike-detect need it)
    rds_out = rds_stack.outputs()
    rds_host = rds_out.get("DbEndpointAddress")
    if rds_host:
        params["RdsHost"] = rds_host

    # Redis endpoint
    redis_out = Stack(tier="20", name="redis", template="").outputs()
    redis_host = redis_out.get("RedisEndpoint")
    if redis_host:
        params["RedisHost"] = redis_host

    # Step Functions ARN (forecast-trigger Lambda)
    sf_arn = Stack(tier="99", name="step-functions", template="").outputs().get("Etl3StateMachineArn", "")
    if sf_arn:
        params["StepFunctionsArn"] = sf_arn

    # Secret ARNs: prefer per-pod, fall back to master-password (Tier 00 secrets)
    sm = boto3.client("secretsmanager", region_name=Config.REGION)
    def _secret_arn(name: str) -> str:
        try:
            return sm.describe_secret(SecretId=name)["ARN"]
        except sm.exceptions.ResourceNotFoundException:
            return ""
    master_arn = _secret_arn(f"{Config.PROJECT_NAME}/rds/master-password")
    pos_arn = _secret_arn(f"{Config.PROJECT_NAME}/rds/pos_ingestor") or master_arn
    spike_arn = _secret_arn(f"{Config.PROJECT_NAME}/rds/spike_detect") or master_arn
    if pos_arn:
        params["PosIngestorSecretArn"] = pos_arn
    if spike_arn:
        params["SpikeDetectSecretArn"] = spike_arn

    log.info(f"  SAM params: {sorted(params)}")

    Stack(tier="99", name="lambdas",
          template="99-serverless/sam-template.yaml",
          parameters=params,
          capabilities=["CAPABILITY_NAMED_IAM", "CAPABILITY_AUTO_EXPAND", "CAPABILITY_IAM"]
          ).deploy()

    out = Stack(tier="99", name="lambdas", template="").outputs()
    log.info(f"secret-forwarder API: {out.get('SecretForwarderApiUrl', '?')}")
    log.step("=== task-lambdas  ===")


def destroy() -> None:
    log.step("=== task-lambdas-down ===")
    Stack(tier="99", name="lambdas", template="").destroy()
    log.step("=== task-lambdas-down  ===")
