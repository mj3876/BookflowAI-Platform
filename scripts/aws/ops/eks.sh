#!/usr/bin/env bash
# eks.sh · MSA Pod 전체 (cluster + IRSA + nodegroup + addons + helm + manifests + Secret sync)
# 의존: base + (peering | cross-cloud)

set -euo pipefail
export PYTHONUTF8=1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

ACTION="${1:-up}"
load_env
acquire_lock "eks"
init_log "eks" "$ACTION"
pre_flight yes   # kubectl + helm 필요

INFRA="$PROJECT_ROOT/infra/aws"

case "$ACTION" in
up)
  step "eks.sh up"

  step "1. eks-cluster"
  cfn_deploy bookflow-30-eks-cluster "$INFRA/30-compute-cluster/eks-cluster.yaml"

  step "2. 2 IRSA 병렬"
  cfn_parallel_deploy <<EOF
bookflow-30-eks-eso-irsa|$INFRA/30-compute-cluster/eks-eso-irsa.yaml
bookflow-30-eks-alb-controller-irsa|$INFRA/30-compute-cluster/eks-alb-controller-irsa.yaml
EOF

  # nodegroup + addons 병렬 — VPC CNI 가 없으면 nodes NotReady 로 nodegroup CFN deadlock.
  # cluster recreate 안 한 경우(원래 addon detach 상태) 특히 critical. 둘 다 EKS cluster 만 의존.
  step "3+4. eks-nodegroup + eks-addons 병렬 (CNI 가 nodes Ready 보장)"
  cfn_parallel_deploy <<EOF
bookflow-40-eks-nodegroup|$INFRA/40-compute-runtime/eks-nodegroup.yaml
bookflow-40-eks-addons|$INFRA/40-compute-runtime/eks-addons.yaml
EOF

  step "5. kubectl 인증 + helm + manifests + Secret sync"
  py "$PROJECT_ROOT/scripts/aws/bookflow.py" task eks-addons

  state_write "eks" "up"
  step "eks.sh up done"
  ;;
down)
  step "eks.sh down · K8s LB cleanup + helm uninstall + CFN 4 stack delete"

  # K8s LoadBalancer Service 명시적 정리 (orphan NLB 방지 · 2026-05-07 incident)
  if kubectl get nodes >/dev/null 2>&1; then
    log "K8s LoadBalancer Service 정리"
    kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}' 2>/dev/null \
      | while IFS=/ read ns name; do
          [ -z "$ns" ] && continue
          log "  delete svc/$name -n $ns"
          kubectl delete svc -n "$ns" "$name" --timeout=60s --ignore-not-found || true
        done
    log "  NLB ENI release 30s 대기"
    sleep 30
  else
    log "kubectl 미연결 — K8s LB cleanup skip (이미 cluster 없음)"
  fi

  py "$PROJECT_ROOT/scripts/aws/bookflow.py" task eks-addons --down || true
  cfn_bulk_delete "bookflow-40-eks-addons" "bookflow-00-"
  cfn_bulk_delete "bookflow-40-eks-nodegroup" "bookflow-00-"
  cfn_bulk_delete "bookflow-30-eks-" "bookflow-00-"
  state_write "eks" "down"
  step "eks.sh down done"
  ;;
*)
  err "usage: $0 up|down"; exit 2 ;;
esac
