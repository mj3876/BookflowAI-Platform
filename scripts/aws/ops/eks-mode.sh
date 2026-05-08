#!/usr/bin/env bash
# eks-mode.sh · EKS endpoint public ↔ private 전환 (+ client-vpn 자동)
# 사용: ./eks-mode.sh public | private
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
MODE="${1:-}"
load_env; pre_flight
INFRA="$PROJECT_ROOT/infra/aws"

case "$MODE" in
public)
  step "eks-mode.sh public · EKS public endpoint + client-vpn destroy"
  cfn_deploy bookflow-30-eks-cluster "$INFRA/30-compute-cluster/eks-cluster.yaml" \
    "EKSEndpointPublicAccess=true" "EKSEndpointPrivateAccess=true"
  py "$PROJECT_ROOT/scripts/aws/bookflow.py" task client-vpn --down 2>/dev/null || true
  state_write "eks-mode" "public"
  ;;
private)
  step "eks-mode.sh private · EKS private endpoint + client-vpn deploy"
  cfn_deploy bookflow-30-eks-cluster "$INFRA/30-compute-cluster/eks-cluster.yaml" \
    "EKSEndpointPublicAccess=false" "EKSEndpointPrivateAccess=true"
  py "$PROJECT_ROOT/scripts/aws/bookflow.py" task client-vpn
  log "ovpn 파일 다운로드: AWS Console → Client VPN endpoints → bookflow → Download client config"
  state_write "eks-mode" "private"
  ;;
*)
  err "usage: $0 public|private"; exit 2 ;;
esac
