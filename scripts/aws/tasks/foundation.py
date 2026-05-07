"""Tier 00 Foundation (Day 0 once - permanent resources).

Default STACKS: KMS / IAM / Parameter Store / Secrets / ACM / ECR / CodeStar / S3.
Optional STACKS (audit/observability): CloudTrail / CloudWatch - skipped by default.
Use STACKS_OPTIONAL list to deploy them separately when needed.

`BOOKFLOW_FOUNDATION_SKIP` env var to skip selected stacks (comma-separated short names):
e.g. `BOOKFLOW_FOUNDATION_SKIP=codestar-connection,acm,s3` -> deploy 5 stacks (local CICD mode).
"""
import os

from ..lib import Stack, log


STACKS = [
    ("iam",                 "00-foundation/iam.yaml"),
    ("kms",                 "00-foundation/kms.yaml"),
    ("parameter-store",     "00-foundation/parameter-store.yaml"),
    ("secrets",             "00-foundation/secrets.yaml"),
    ("acm",                 "00-foundation/acm.yaml"),
    ("ecr",                 "00-foundation/ecr.yaml"),
    ("codestar-connection", "00-foundation/codestar-connection.yaml"),
    ("s3",                  "00-foundation/s3.yaml"),
]

# audit/observability - audit bucket (S3 Object Lock 90d) - deploy separately
STACKS_OPTIONAL = [
    ("cloudtrail",          "00-foundation/cloudtrail.yaml"),
    ("cloudwatch",          "00-foundation/cloudwatch.yaml"),
]


def _skip_set() -> set[str]:
    raw = os.environ.get("BOOKFLOW_FOUNDATION_SKIP", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def deploy() -> None:
    log.step("=== Phase 0 Foundation Deploy (permanent / Day 0 once) ===")
    skip = _skip_set()
    for name, template in STACKS:
        if name in skip:
            log.info(f"  skip {name} (BOOKFLOW_FOUNDATION_SKIP)")
            continue
        Stack(tier="00", name=name, template=template).deploy()
    if "codestar-connection" not in skip:
        log.warn("CodeStar Connection created in PENDING - manual Activate via Console required")
    log.warn("CloudTrail / CloudWatch default skip - deploy via STACKS_OPTIONAL when needed")
    log.warn("Audit S3 bucket: phase0 default skip - enable via `s3.yaml` EnableAuditBucket=true")
    log.step("=== Tier 00 Foundation deploy done ===")


def destroy() -> None:
    log.step("=== Tier 00 Foundation Destroy (permanent resources WARNING) ===")
    # OPTIONAL also cleaned up (destroy if exists / no-op otherwise)
    for name, _template in reversed(STACKS + STACKS_OPTIONAL):
        Stack(tier="00", name=name, template="").destroy()
    log.step("=== Tier 00 destroy done ===")
