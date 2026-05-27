#!/usr/bin/env bash
# start-day.sh · 매일 실행
# 흐름: base → network(+vpn-attach) → SAM+ClientVPN 병렬 → seed+VPN연결 병렬
#        → 4서비스 병렬 → CICD → eks-addons resync
# 발표일: + ./scripts/ops/network-mode.sh tgw + ./scripts/ops/eks-mode.sh private
#
# Env:
#   BOOKFLOW_ENV=admin|deploy (default deploy)
#   BOOKFLOW_SKIP_CLIENT_VPN=1  Client VPN 연결 단계 건너뜀 (디버그용)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
load_env
init_log "start-day" "up"
pre_flight

T0=$(date +%s)

# ── Client VPN 연결 함수 ──────────────────────────────────────────────────────
# DNS 서버가 10.0.0.2(VPC DNS)이면 연결됨으로 판정.
# 미연결 시 openvpn.exe daemon 으로 시작 후 최대 60s 대기.
# 이 함수는 step 4 에서 seed.sh 와 병렬로 실행됨.
_connect_client_vpn() {
  if [[ "${BOOKFLOW_SKIP_CLIENT_VPN:-0}" == "1" ]]; then
    log "Client VPN skip (BOOKFLOW_SKIP_CLIENT_VPN=1)"
    return 0
  fi
  local dns_srv
  dns_srv=$(nslookup oauth2.googleapis.com 2>/dev/null \
    | awk '/^Server:/{print $2; exit}' || echo "")
  if [[ "$dns_srv" == "10.0.0.2" ]]; then
    log "Client VPN 이미 연결됨 (DNS: 10.0.0.2)"
    return 0
  fi
  local sync_script="$SCRIPT_DIR/../../../scripts/sync-client-vpn-profile.ps1"
  if command -v powershell.exe >/dev/null 2>&1 && [[ -f "$sync_script" ]]; then
    powershell.exe -ExecutionPolicy Bypass -File "$(cygpath -w "$sync_script")" >/dev/null 2>&1 \
      || warn "Client VPN profile sync failed; continuing with existing ovpn"
  fi

  local ovpn="$SCRIPT_DIR/../../../_client_vpn_certs_v2/bookflow-client-desktop-1.ovpn"
  local openvpn_bin=""
  for _p in \
    "/c/Program Files/OpenVPN/bin/openvpn.exe" \
    "/c/Program Files (x86)/OpenVPN/bin/openvpn.exe"; do
    [[ -f "$_p" ]] && { openvpn_bin="$_p"; break; }
  done
  if [[ -z "$openvpn_bin" ]]; then
    warn "Client VPN: openvpn.exe 없음 — OpenVPN GUI 수동 연결 필요"
    return 0
  fi
  if [[ ! -f "$ovpn" ]]; then
    warn "Client VPN: $ovpn 없음 — scripts/aws/setup-vpn.sh 먼저 실행"
    return 0
  fi
  log "Client VPN 연결 시작 (daemon)..."
  local vpn_log="$LOG_DIR/$(date +%Y-%m-%d)_client-vpn.log"
  "$openvpn_bin" \
    --config "$(cygpath -w "$ovpn")" \
    --daemon \
    --log "$(cygpath -w "$vpn_log")" \
    2>/dev/null || true
  local waited=0
  while [[ $waited -lt 60 ]]; do
    sleep 5; waited=$((waited + 5))
    dns_srv=$(nslookup oauth2.googleapis.com 2>/dev/null \
      | awk '/^Server:/{print $2; exit}' || echo "")
    if [[ "$dns_srv" == "10.0.0.2" ]]; then
      log "Client VPN 연결됨 (${waited}s)"
      return 0
    fi
  done
  warn "Client VPN 연결 타임아웃 (60s) — kubectl 접근 실패할 수 있음"
  warn "  수동: OpenVPN GUI → bookflow-client-desktop-1.ovpn 연결"
}

step "1/7 base · prereq"
"$SCRIPT_DIR/ops/base.sh" up

# TGW 활성 시: cross-cloud 스택은 그대로 유지하되
# VPN attachment → TGW RT association/propagation 을 idempotent 재확인.
# 새 VPN connection 이 추가됐을 때(auth_pod 스택 업데이트 등) 자동 정정.
step "2/7 network · TGW 활성이면 vpn-attach 재확인 · 아니면 peering"
TGW_ACTIVE=$(AWS_PROFILE="${AWS_PROFILE:-bookflow-deploy}" AWS_REGION="${AWS_REGION:-ap-northeast-1}" \
  aws cloudformation describe-stacks --stack-name bookflow-60-tgw \
  --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "NONE")
if [[ "$TGW_ACTIVE" == *"COMPLETE"* ]]; then
  log "TGW 모드 감지 — VPN attachment association/propagation 재확인"
  "$SCRIPT_DIR/ops/tgw-vpn-attach.sh" || warn "tgw-vpn-attach 경고 (already done 이면 정상)"
else
  "$SCRIPT_DIR/ops/peering.sh" up
fi

# SAM + Client VPN 병렬 배포 — base.sh 완료 후 독립적으로 실행 가능.
# CICD(eks·publisher)는 EKS cluster·Publisher ASG ImportValue 의존 → step 5 완료 후 step 6에서 배포.
# etl.sh 내부의 lambdas 단계는 BOOKFLOW_LAMBDAS_DEPLOYED=1 이면 skip.
step "3/8 SAM (99-serverless) + Client VPN 병렬 배포"
py "$PROJECT_ROOT/scripts/aws/bookflow.py" task lambdas    &  LAMBDAS_PID=$!
py "$PROJECT_ROOT/scripts/aws/bookflow.py" task client-vpn &  CVPN_PID=$!
if ! wait $LAMBDAS_PID; then
  err "SAM 배포 실패 — logs/ 확인"
  wait $CVPN_PID || true
  exit 1
fi
export BOOKFLOW_LAMBDAS_DEPLOYED=1
wait $CVPN_PID || warn "client-vpn 배포 경고 (이미 존재하면 정상)"

# seed 를 eks 병렬 실행 전에 완료 — eks.sh 내부 BOOKFLOW_SKIP_RDS_SYNC=1 은 그대로이나
# step 6 resync 시 role/password 가 이미 확정된 상태로 실행되므로 ALTER ROLE 1회로 충분.
# Client VPN 연결(~30s)을 seed(~2분)와 병렬 실행해 전체 대기 시간 최소화.
step "4/8 seed + Client VPN 병렬 준비 (parquet→RDS · VPN 연결)"
"$SCRIPT_DIR/ops/seed.sh" up &  SEED_PID=$!
_connect_client_vpn           &  VPN_PID=$!
if ! wait $SEED_PID; then
  err "seed 실패 — eks 진행 불가. logs/ 확인 후 ops/seed.sh up 재시도"
  wait $VPN_PID || true
  exit 1
fi
wait $VPN_PID || true  # VPN 연결 실패는 치명적이지 않음 (warn 후 계속)

step "5/8 4 서비스 병렬 (eks · ecs · publisher · etl)"
"$SCRIPT_DIR/ops/eks.sh" up &        EKS_PID=$!
"$SCRIPT_DIR/ops/ecs.sh" up &        ECS_PID=$!
"$SCRIPT_DIR/ops/publisher.sh" up &  PUB_PID=$!
"$SCRIPT_DIR/ops/etl.sh" up &        ETL_PID=$!

FAILED=0
for pid in $EKS_PID $ECS_PID $PUB_PID $ETL_PID; do
  if ! wait $pid; then FAILED=$((FAILED + 1)); fi
done
if [ $FAILED -gt 0 ]; then
  err "$FAILED service(s) failed — logs/ 확인 후 개별 스크립트로 재시도"
  exit 1
fi

# CICD: eks-pipeline(CodeBuildEksAccessEntry→bookflow-eks-cluster-name)·publisher-codedeploy
#        (PublisherDeploymentGroup→bookflow-publisher-asg-name) 둘 다 step 5 완료 후 ImportValue 충족.
step "6/8 CICD 3 CodePipeline (step 5 완료 후 · ImportValue 의존 해소)"
"$SCRIPT_DIR/ops/cicd.sh" up || warn "cicd 배포 경고 (이미 존재하면 정상)"

# eks.sh 내부 BOOKFLOW_SKIP_RDS_SYNC=1 로 ALTER ROLE 건너뜀.
# seed 가 step 4 에서 이미 완료됐으므로 role/password 확정 후 full resync.
step "7/8 eks-addons resync · ALTER ROLE 11 + 7 pod rollout (DB pool 정합)"
py "$PROJECT_ROOT/scripts/aws/bookflow.py" task eks-addons || warn "eks-addons resync 실패 (이미 정합이면 무시)"

step "8/8 eks-addons resync 완료 · 인프라 배포 끝"

ELAPSED=$(( $(date +%s) - T0 ))
state_write "last-start-day" "$(date +%Y-%m-%dT%H:%M:%S)"
state_write "last-start-elapsed" "$ELAPSED"
echo ""
echo "═══ start-day done · ${ELAPSED}s ═══"
echo "  발표일 추가:    ./scripts/ops/network-mode.sh tgw && ./scripts/ops/eks-mode.sh private"
