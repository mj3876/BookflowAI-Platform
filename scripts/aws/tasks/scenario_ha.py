"""task-scenario-ha · RDS Multi-AZ + Redis ReplicationGroup .

Revert: --revert  single-AZ + single-node .
"""
from ..lib import Stack, log


def deploy() -> None:
    log.step("=== HA  · RDS Multi-AZ + Redis Replication ===")
    log.warn("Redis SingleNode → ReplicationGroup  resource replacement (cache )")
    log.warn("RDS Multi-AZ  in-place modify (~5-10)")

    Stack(tier="20", name="rds", template="20-data-persistent/rds.yaml",
          parameters={"EnableMultiAz": "true"}).deploy()
    Stack(tier="20", name="redis", template="20-data-persistent/redis.yaml",
          parameters={"EnableReplication": "true"}).deploy()

    log.step("=== HA   ===")


def destroy() -> None:
    """Revert: HA →   (Single-AZ · SingleNode)."""
    log.step("=== HA revert · Single-AZ + SingleNode  ===")
    Stack(tier="20", name="rds", template="20-data-persistent/rds.yaml",
          parameters={"EnableMultiAz": "false"}).deploy()
    Stack(tier="20", name="redis", template="20-data-persistent/redis.yaml",
          parameters={"EnableReplication": "false"}).deploy()
    log.step("=== HA revert  ===")
