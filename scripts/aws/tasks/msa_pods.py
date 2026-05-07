"""task-msa-pods · EKS Cluster + IRSA + NodeGroup + Addons + endpoints + peering.

Dependency-aware parallel:
  Wave A: 3 endpoints/peerings + eks-cluster (parallel · all need only vpc-bookflow-ai)
  Wave B: 2 IRSAs + eks-nodegroup (parallel · all need eks-cluster)
  Wave C: eks-addons (needs nodegroup)
"""
from ..lib import Stack, log
from ..lib.parallel import parallel_deploy


def deploy() -> None:
    log.step("=== task-msa-pods · EKS + endpoints + peering (parallel) ===")

    if not Stack(tier="10", name="vpc-bookflow-ai", template="").exists():
        log.err("vpc-bookflow-ai missing · run base-up first"); raise SystemExit(1)

    # Wave A: vpc-bookflow-ai 만 의존 · 모두 동시 (~15min for EKS cluster)
    parallel_deploy([
        Stack(tier="10", name="endpoints-bookflow-ai",
              template="10-network-core/endpoints/endpoints-bookflow-ai.yaml"),
        Stack(tier="10", name="peering-bookflow-ai-data",
              template="10-network-core/peering/bookflow-ai-data.yaml"),
        Stack(tier="10", name="peering-bookflow-ai-egress",
              template="10-network-core/peering/bookflow-ai-egress.yaml"),
        Stack(tier="30", name="eks-cluster",
              template="30-compute-cluster/eks-cluster.yaml"),
    ], label="3 peerings/endpoints + eks-cluster")

    # Wave B: eks-cluster 의존 · 3종 동시 (~5min)
    parallel_deploy([
        Stack(tier="30", name="eks-alb-controller-irsa",
              template="30-compute-cluster/eks-alb-controller-irsa.yaml"),
        Stack(tier="30", name="eks-eso-irsa",
              template="30-compute-cluster/eks-eso-irsa.yaml"),
        Stack(tier="40", name="eks-nodegroup",
              template="40-compute-runtime/eks-nodegroup.yaml"),
    ], label="2 IRSAs + eks-nodegroup")

    # Wave C: nodegroup 의존
    Stack(tier="40", name="eks-addons",
          template="40-compute-runtime/eks-addons.yaml").deploy()

    log.step("=== task-msa-pods done ===")
    log.info("kubeconfig: aws eks update-kubeconfig --name bookflow-eks --region ap-northeast-1")


def destroy() -> None:
    log.step("=== task-msa-pods-down ===")
    Stack(tier="40", name="eks-addons", template="").destroy()
    Stack(tier="40", name="eks-nodegroup", template="").destroy()
    Stack(tier="30", name="eks-eso-irsa", template="").destroy()
    Stack(tier="30", name="eks-alb-controller-irsa", template="").destroy()
    Stack(tier="30", name="eks-cluster", template="").destroy()
    Stack(tier="10", name="peering-bookflow-ai-egress", template="").destroy()
    Stack(tier="10", name="peering-bookflow-ai-data", template="").destroy()
    Stack(tier="10", name="endpoints-bookflow-ai", template="").destroy()
    log.step("=== task-msa-pods-down  ===")
