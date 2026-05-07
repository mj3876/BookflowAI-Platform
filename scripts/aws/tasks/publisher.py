"""task-publisher · External ALB + WAF + Publisher ASG + ECS inventory-api."""
from ..lib import Stack, log


def deploy() -> None:
    log.step("=== task-publisher · ALB + WAF + Publisher + inventory-api ===")
    if not Stack(tier="10", name="vpc-egress", template="").exists():
        log.err("vpc-egress "); raise SystemExit(1)
    if not Stack(tier="20", name="rds", template="").exists():
        log.warn("RDS  · inventory-api DB    task-data ")

    Stack(tier="10", name="peering-egress-data",
          template="10-network-core/peering/egress-data.yaml").deploy()

    alb = Stack(tier="50", name="alb-external", template="50-network-traffic/alb-external.yaml")
    alb.deploy()
    Stack(tier="50", name="waf", template="50-network-traffic/waf.yaml").deploy()

    out = alb.outputs()
    blue_tg = out.get("PublisherBlueTgArn")
    inv_tg = out.get("InventoryApiTgArn")
    if not blue_tg or not inv_tg:
        log.err(f"ALB outputs  (blue={blue_tg}, inv={inv_tg})"); raise SystemExit(1)
    log.info(f"PublisherBlueTg: {blue_tg}")
    log.info(f"InventoryApiTg: {inv_tg}")

    Stack(tier="40", name="publisher-asg",
          template="40-compute-runtime/publisher-asg.yaml",
          parameters={"TargetGroupArn": blue_tg}).deploy()
    Stack(tier="40", name="ecs-inventory-api",
          template="40-compute-runtime/ecs-inventory-api.yaml",
          parameters={"TargetGroupArn": inv_tg}).deploy()

    log.info(f"ALB DNS: {out.get('AlbDnsName', '?')}")
    log.step("=== task-publisher  ===")


def destroy() -> None:
    log.step("=== task-publisher-down ===")
    Stack(tier="40", name="ecs-inventory-api", template="").destroy()
    Stack(tier="40", name="publisher-asg", template="").destroy()
    Stack(tier="50", name="waf", template="").destroy()
    Stack(tier="50", name="alb-external", template="").destroy()
    Stack(tier="10", name="peering-egress-data", template="").destroy()
    log.step("=== task-publisher-down  ===")
