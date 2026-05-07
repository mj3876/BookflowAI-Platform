"""task-data · Tier 20 RDS + Redis + Kinesis (모두 독립 → 병렬)."""
from ..lib import Stack, log
from ..lib.parallel import parallel_deploy


def deploy() -> None:
    log.step("=== task-data · RDS + Redis + Kinesis (parallel) ===")
    if not Stack(tier="10", name="vpc-data", template="").exists():
        log.err("Tier 10 vpc-data missing · run base-up first")
        raise SystemExit(1)

    parallel_deploy([
        Stack(tier="20", name="rds", template="20-data-persistent/rds.yaml",
              parameters={"EnableMultiAz": "false"}),
        Stack(tier="20", name="redis", template="20-data-persistent/redis.yaml",
              parameters={"EnableReplication": "false"}),
        Stack(tier="20", name="kinesis", template="20-data-persistent/kinesis.yaml"),
    ], label="RDS + Redis + Kinesis")
    log.step("=== task-data done ===")


def destroy() -> None:
    log.step("=== task-data-down ===")
    Stack(tier="20", name="kinesis", template="").destroy()
    Stack(tier="20", name="redis", template="").destroy()
    Stack(tier="20", name="rds", template="").destroy()
    log.step("=== task-data-down  ===")
