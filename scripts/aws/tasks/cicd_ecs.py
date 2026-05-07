"""cicd-ecs · CodePipeline + CodeBuild for BookFlowAI-Apps ecs-sims

Stack : bookflow-cicd-ecs
Template:  cicd/codepipeline/ecs-pipeline.yaml

:
  ecs-sims/** push → CodeStar webhook → CodeBuild
    1. docker build × 3 (online-sim · offline-sim · inventory-api) → ECR
    2. aws ecs update-service --force-new-deployment (3 service)
  → Fargate task replacement (rolling)

 :
  - Tier 00 codestar-connection · ecr (online-sim · offline-sim · inventory-api)
  - Tier 30 ecs-cluster (bookflow-ecs ACTIVE)
  - Tier 40 ecs-online-sim · ecs-offline-sim · ecs-inventory-api Service  (taskdef  :latest )

: 🟡   deploy
"""
from ..lib import Stack, log
from ..lib.config import Config

CICD_ROOT = Config.REPO_ROOT / "cicd" / "codepipeline"


def deploy() -> None:
    log.step("=== cicd-ecs · CodePipeline + CodeBuild deploy ===")
    Stack(
        tier="cicd",
        name="ecs",
        template="ecs-pipeline.yaml",
        template_root=CICD_ROOT,
    ).deploy()
    log.step("=== cicd-ecs deploy  ===")


def destroy() -> None:
    log.step("=== cicd-ecs destroy ===")
    Stack(tier="cicd", name="ecs", template="").destroy()
    log.step("=== cicd-ecs destroy  ===")
