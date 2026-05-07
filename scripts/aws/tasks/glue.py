"""task-glue · Tier 99-glue (Catalog + 6 Jobs + Step Functions ETL3)."""
import boto3
from ..lib import Stack, log, Config


def deploy() -> None:
    log.step("=== task-glue · Glue Catalog + 6 Jobs + Step Functions ===")

    Stack(tier="99", name="glue-catalog",
          template="99-glue/glue-catalog.yaml").deploy()
    Stack(tier="99", name="step-functions",
          template="99-glue/step-functions.yaml").deploy()

    sf_arn = Stack(tier="99", name="step-functions", template="").outputs().get("Etl3StateMachineArn")

    if Stack(tier="99", name="lambdas", template="").exists() and sf_arn:
        log.info("task-lambdas · forecast-trigger SF ARN 업데이트 중...")
        lm = boto3.client("lambda", region_name=Config.REGION)
        fn_name = f"{Config.PROJECT_NAME}-forecast-trigger"
        try:
            cur = lm.get_function_configuration(FunctionName=fn_name)
            env_vars = cur.get("Environment", {}).get("Variables", {})
            env_vars["STEP_FN_ARN"] = sf_arn
            lm.update_function_configuration(
                FunctionName=fn_name,
                Environment={"Variables": env_vars},
            )
            log.info(f"  forecast-trigger STEP_FN_ARN 설정 완료")
        except Exception as e:
            log.info(f"  forecast-trigger 업데이트 스킵: {e}")
    else:
        log.info("task-lambdas 미배포 · SF ARN 업데이트 스킵")

    log.step("=== task-glue  ===")
    if sf_arn:
        log.info(f"ETL3 SF ARN: {sf_arn}")


def destroy() -> None:
    log.step("=== task-glue-down ===")
    Stack(tier="99", name="step-functions", template="").destroy()
    Stack(tier="99", name="glue-catalog", template="").destroy()
    log.step("=== task-glue-down  ===")
