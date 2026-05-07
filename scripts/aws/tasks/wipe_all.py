"""wipe-all ·   (Tier 00  ) destroy.    .

⚠️  confirmation  · S3  / ECR    .
"""
from ..lib import log
from . import base, cicd_ecs, cicd_eks, foundation


def deploy() -> None:
    raise SystemExit("wipe-all  destroy . `phase0` + `task`  deploy.")


def destroy() -> None:
    log.step("=== WIPE ALL ·  BOOKFLOW   ===")
    log.warn("S3  / ECR  / KMS pending / ACM IN_USE ·   ")
    confirm = input(" ? 'WIPE EVERYTHING'  → ").strip()
    if confirm != "WIPE EVERYTHING":
        log.info("")
        return

    log.info("1. cicd-eks-down · cicd-ecs-down (CICD pipeline · ImportValue  )")
    for mod in (cicd_eks, cicd_ecs):
        try:
            mod.destroy()
        except Exception as e:
            log.warn(f"  {mod.__name__} destroy    : {e}")

    log.info("2. base-down (Tier 10-99)")
    base.destroy()
    log.info("3. phase0-foundation-down (Tier 00 )")
    foundation.destroy()
    log.step("=== WIPE ALL  ===")
