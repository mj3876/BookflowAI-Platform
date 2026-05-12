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

    # TGW 모드면 peering CIDR 가 TGW route 와 충돌 → peering 스킵 (etl_streaming.py 와 동일 패턴)
    tgw_active = Stack(tier="60", name="tgw-vpc-routes", template="").exists()
    wave_a = [
        Stack(tier="10", name="endpoints-bookflow-ai",
              template="10-network-core/endpoints/endpoints-bookflow-ai.yaml"),
        Stack(tier="30", name="eks-cluster",
              template="30-compute-cluster/eks-cluster.yaml"),
    ]
    if tgw_active:
        log.info("bookflow-60-tgw-vpc-routes 활성 → peering-bookflow-ai-{data,egress} 스킵 (TGW 라우팅)")
    else:
        wave_a += [
            Stack(tier="10", name="peering-bookflow-ai-data",
                  template="10-network-core/peering/bookflow-ai-data.yaml"),
            Stack(tier="10", name="peering-bookflow-ai-egress",
                  template="10-network-core/peering/bookflow-ai-egress.yaml"),
        ]
    parallel_deploy(wave_a, label=f"{len(wave_a)} stacks (eks-cluster + endpoints" + ('' if tgw_active else ' + 2 peerings') + ')')

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
    # TGW 모드면 peering 자체가 없어 destroy noop · leftover 있으면 자동 정리
    for n in ("peering-bookflow-ai-egress", "peering-bookflow-ai-data"):
        st = Stack(tier="10", name=n, template="")
        if st.exists():
            st.destroy()
    Stack(tier="10", name="endpoints-bookflow-ai", template="").destroy()
    log.step("=== task-msa-pods-down  ===")
