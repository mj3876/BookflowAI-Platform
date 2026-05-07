"""task-etl-streaming · ECS sims (online + offline) + endpoints-sales-data."""
from ..lib import Stack, log


def deploy() -> None:
    log.step("=== task-etl-streaming · ECS sims ===")
    if not Stack(tier="10", name="vpc-sales-data", template="").exists():
        log.err("vpc-sales-data  · base-up "); raise SystemExit(1)
    if not Stack(tier="20", name="kinesis", template="").exists():
        log.err("kinesis  · task-data "); raise SystemExit(1)
    if not Stack(tier="30", name="ecs-cluster", template="").exists():
        log.err("ecs-cluster  · base-up "); raise SystemExit(1)

    Stack(tier="10", name="endpoints-sales-data",
          template="10-network-core/endpoints/endpoints-sales-data.yaml").deploy()

    # bookflow-60-tgw-vpc-routes 가 10.1.0.0/16 · 10.2.0.0/16 라우트를 이미 소유하므로
    # peering 스택이 같은 CIDR 재생성 시 CFN "managed by another stack" 오류 발생
    if Stack(tier="60", name="tgw-vpc-routes", template="").exists():
        log.info("bookflow-60-tgw-vpc-routes 활성 → peering-sales-data-egress 스킵 (TGW가 라우팅 담당)")
    else:
        Stack(tier="10", name="peering-sales-data-egress",
              template="10-network-core/peering/sales-data-egress.yaml").deploy()
    Stack(tier="40", name="ecs-online-sim",
          template="40-compute-runtime/ecs-online-sim.yaml").deploy()
    Stack(tier="40", name="ecs-offline-sim",
          template="40-compute-runtime/ecs-offline-sim.yaml").deploy()

    log.step("=== task-etl-streaming  ===")


def destroy() -> None:
    log.step("=== task-etl-streaming-down ===")
    Stack(tier="40", name="ecs-offline-sim", template="").destroy()
    Stack(tier="40", name="ecs-online-sim", template="").destroy()
    Stack(tier="10", name="peering-sales-data-egress", template="").destroy()
    Stack(tier="10", name="endpoints-sales-data", template="").destroy()
    log.step("=== task-etl-streaming-down  ===")
