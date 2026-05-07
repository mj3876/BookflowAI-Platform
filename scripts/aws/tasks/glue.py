"""task-glue · Tier 99-glue (Catalog + 6 Jobs + Step Functions ETL3)."""
from ..lib import Stack, log


def deploy() -> None:
    log.step("=== task-glue · Glue Catalog + 6 Jobs + Step Functions ===")

    Stack(tier="99", name="glue-catalog",
          template="99-glue/glue-catalog.yaml").deploy()
    Stack(tier="99", name="step-functions",
          template="99-glue/step-functions.yaml").deploy()

    sf_arn = Stack(tier="99", name="step-functions", template="").outputs().get("Etl3StateMachineArn")

    if Stack(tier="99", name="lambdas", template="").exists() and sf_arn:
        log.info("task-lambdas  deploy  → forecast-trigger  SF ARN ")
        Stack(tier="99", name="lambdas",
              template="99-serverless/sam-template.yaml",
              parameters={"StepFunctionsArn": sf_arn},
              capabilities=["CAPABILITY_NAMED_IAM", "CAPABILITY_AUTO_EXPAND", "CAPABILITY_IAM"]
              ).deploy()
    else:
        log.info("task-lambdas  ·  task-lambdas   SF ARN  ")

    log.step("=== task-glue  ===")
    if sf_arn:
        log.info(f"ETL3 SF ARN: {sf_arn}")


def destroy() -> None:
    log.step("=== task-glue-down ===")
    Stack(tier="99", name="step-functions", template="").destroy()
    Stack(tier="99", name="glue-catalog", template="").destroy()
    log.step("=== task-glue-down  ===")
