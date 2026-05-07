"""base-up / base-down ·  Tier 10 VPC × 5 + Tier 30 ECS cluster (모두 독립 → 병렬)."""
from ..lib import Stack, log
from ..lib.parallel import parallel_deploy

# 5 VPC (sales-data · egress · data · bookflow-ai · ansible) — 모두 서로 독립
TIER10 = [
    ("vpc-sales-data",   "10-network-core/vpc-sales-data.yaml"),
    ("vpc-egress",       "10-network-core/vpc-egress.yaml"),
    ("vpc-data",         "10-network-core/vpc-data.yaml"),
    ("vpc-bookflow-ai",  "10-network-core/vpc-bookflow-ai.yaml"),
    ("vpc-ansible",      "10-network-core/vpc-ansible.yaml"),
]

TIER30 = [
    ("ecs-cluster",  "30-compute-cluster/ecs-cluster.yaml"),
]


def deploy() -> None:
    log.step("=== base-up · Tier 10 VPC × 5 + Tier 30 ECS cluster (parallel) ===")
    # VPC × 5 + ECS cluster 모두 독립 → 6 stacks 동시 deploy (~3-5min)
    stacks = (
        [Stack(tier="10", name=n, template=t) for n, t in TIER10]
        + [Stack(tier="30", name=n, template=t) for n, t in TIER30]
    )
    parallel_deploy(stacks, label=f"{len(stacks)} VPCs + ECS cluster")
    log.step("=== base-up done ===")


def destroy() -> None:
    """  18:00 · Tier 10-60 + 99  destroy (Tier 00  )."""
    # K8s workload (helm releases) must be uninstalled before EKS stack deletion.
    # otherwise CFN delete leaves orphaned LoadBalancers / PVCs / namespaces.
    try:
        from . import mocks
        mocks.destroy()
    except SystemExit:
        log.warn("mocks helm cleanup skipped (helm/kubectl missing or no cluster)")
    except Exception as e:
        log.warn(f"mocks helm cleanup failed: {e} (continuing)")

    log.step("=== base-down · Tier 10-99  destroy ===")

    DOWN_ORDER = {
        "99": ["step-functions", "glue-catalog", "lambdas"],
        "60": ["client-vpn", "vpn-site-to-site", "tgw"],
        "50": ["waf", "alb-external", "nat-gateway"],
        "40": ["ecs-online-sim", "ecs-offline-sim", "ecs-inventory-api",
               "publisher-asg", "eks-addons", "eks-nodegroup"],
        "30": ["eks-eso-irsa", "eks-alb-controller-irsa", "eks-cluster",
               "ansible-node", "ecs-cluster"],
        "20": ["kinesis", "redis", "rds"],
        "10": [
            "peering-bookflow-ai-data", "peering-bookflow-ai-egress",
            "peering-egress-data", "peering-sales-data-egress", "peering-ansible-data",
            "endpoints-bookflow-ai", "endpoints-sales-data", "endpoints-ansible",
            "route53", "customer-gateway",
            "vpc-bookflow-ai", "vpc-sales-data", "vpc-egress", "vpc-data", "vpc-ansible",
        ],
    }

    for tier, names in DOWN_ORDER.items():
        for name in names:
            Stack(tier=tier, name=name, template="").destroy()

    log.step("=== base-down  ·  $0 (Tier 00 ) ===")
