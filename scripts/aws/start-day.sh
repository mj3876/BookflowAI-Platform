#!/usr/bin/env bash
# start-day.sh · 매일 실행
# 흐름: base → network(+vpn-attach) → SAM → seed+ClientVPN 병렬
#        → 4서비스 병렬 → eks-addons resync → 데이터 파이프라인
# 데이터 파이프라인 (6단계):
#   aladin-sync Lambda → event-sync Lambda → ETL Step Functions 트리거
#   → Step Functions 완료 대기 → forecast-svc BQ 동기화 → decision-svc plan-daily
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
  local ovpn="$SCRIPT_DIR/../bookflow-client-desktop-1.ovpn"
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

# SAM 먼저 단독 배포 — etl.sh 병렬 실행 전 sam-template.yaml 변경사항을 확정.
# etl.sh 내부의 lambdas 단계는 BOOKFLOW_LAMBDAS_DEPLOYED=1 이면 skip.
step "3/7 SAM (99-serverless) · sam-template.yaml 반영 배포"
py "$PROJECT_ROOT/scripts/aws/bookflow.py" task lambdas
export BOOKFLOW_LAMBDAS_DEPLOYED=1

# seed 를 eks 병렬 실행 전에 완료 — eks.sh 내부 BOOKFLOW_SKIP_RDS_SYNC=1 은 그대로이나
# step 6 resync 시 role/password 가 이미 확정된 상태로 실행되므로 ALTER ROLE 1회로 충분.
# Client VPN 연결(~30s)을 seed(~2분)와 병렬 실행해 전체 대기 시간 최소화.
step "4/7 seed + Client VPN 병렬 준비 (parquet→RDS · VPN 연결)"
"$SCRIPT_DIR/ops/seed.sh" up &  SEED_PID=$!
_connect_client_vpn           &  VPN_PID=$!
if ! wait $SEED_PID; then
  err "seed 실패 — eks 진행 불가. logs/ 확인 후 ops/seed.sh up 재시도"
  wait $VPN_PID || true
  exit 1
fi
wait $VPN_PID || true  # VPN 연결 실패는 치명적이지 않음 (warn 후 계속)

step "5/7 4 서비스 병렬 (eks · ecs · publisher · etl)"
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

# eks.sh 내부 BOOKFLOW_SKIP_RDS_SYNC=1 로 ALTER ROLE 건너뜀.
# seed 가 step 4 에서 이미 완료됐으므로 role/password 확정 후 full resync.
step "6/7 eks-addons resync · ALTER ROLE 11 + 7 pod rollout (DB pool 정합)"
py "$PROJECT_ROOT/scripts/aws/bookflow.py" task eks-addons || warn "eks-addons resync 실패 (이미 정합이면 무시)"

step "7/7 데이터 파이프라인 (aladin-sync → event-sync → ETL → forecast → plan-daily)"

AWS_REGION="${AWS_REGION:-ap-northeast-1}"

# 7-1. aladin-sync Lambda 수동 실행
log "7-1 aladin-sync Lambda 실행..."
aws lambda invoke --function-name bookflow-aladin-sync \
  --invocation-type RequestResponse \
  --payload '{}' /tmp/aladin-sync-out.json \
  --region "$AWS_REGION" --output json \
  --query "StatusCode" 2>&1 | grep -v "^$" || warn "aladin-sync 실패 (계속 진행)"

# 7-2. event-sync Lambda 수동 실행
log "7-2 event-sync Lambda 실행..."
aws lambda invoke --function-name bookflow-event-sync \
  --invocation-type RequestResponse \
  --payload '{}' /tmp/event-sync-out.json \
  --region "$AWS_REGION" --output json \
  --query "StatusCode" 2>&1 | grep -v "^$" || warn "event-sync 실패 (계속 진행)"

# 7-3. ETL Step Functions 실행
SF_ARN=$(aws cloudformation list-exports \
  --query "Exports[?Name=='bookflow-sfn-etl3-arn'].Value" \
  --output text --region "$AWS_REGION" 2>/dev/null || echo "")

if [[ -z "$SF_ARN" ]]; then
  warn "Step Functions ARN 조회 실패 — ETL 건너뜀"
else
  log "7-3 ETL Step Functions 시작: ${SF_ARN}"
  EXEC_ARN=$(aws stepfunctions start-execution \
    --state-machine-arn "$SF_ARN" \
    --input '{"trigger":"start-day"}' \
    --query "executionArn" --output text \
    --region "$AWS_REGION" 2>&1) || { warn "Step Functions 시작 실패 (계속 진행)"; EXEC_ARN=""; }

  # 7-4. Step Functions 완료 대기 (최대 30분)
  if [[ -n "$EXEC_ARN" ]]; then
    log "7-4 ETL 완료 대기 (최대 30분)..."
    WAIT_SECS=0
    while [[ $WAIT_SECS -lt 1800 ]]; do
      SFN_STATUS=$(aws stepfunctions describe-execution \
        --execution-arn "$EXEC_ARN" \
        --query "status" --output text \
        --region "$AWS_REGION" 2>/dev/null || echo "UNKNOWN")
      if [[ "$SFN_STATUS" == "SUCCEEDED" ]]; then
        log "  ETL 완료 (${WAIT_SECS}s)"
        break
      elif [[ "$SFN_STATUS" == "FAILED" || "$SFN_STATUS" == "ABORTED" || "$SFN_STATUS" == "TIMED_OUT" ]]; then
        warn "  ETL 실패: $SFN_STATUS — forecast 단계 건너뜀"
        EXEC_ARN=""
        break
      fi
      sleep 30
      WAIT_SECS=$((WAIT_SECS + 30))
    done
    if [[ $WAIT_SECS -ge 1800 ]]; then
      warn "  ETL 30분 초과 — forecast 단계 건너뜀"
      EXEC_ARN=""
    fi
  fi
fi

# 7-5. forecast-svc BQ 동기화 (ETL 성공 시)
# GCP BigQuery → RDS forecast_cache UPSERT (site-to-site VPN 경유 · PSC 10.50.0.10)
if [[ -n "${EXEC_ARN:-}" ]]; then
  log "7-5 forecast-svc BQ 동기화..."
  kubectl exec -n bookflow deploy/forecast-svc -- python -c "
import sys; sys.path.insert(0, '/app')
from src.routes.forecast import _fetch_bigquery_forecast_rows, _upsert_forecast_rows
from src.settings import settings
from src.db import db_conn
from datetime import datetime, timezone
rows = _fetch_bigquery_forecast_rows(days=settings.bq_refresh_days)
if rows:
    synced_at = datetime.now(timezone.utc)
    with db_conn() as conn:
        with conn.cursor() as cur:
            n = _upsert_forecast_rows(cur, rows, synced_at)
        conn.commit()
    print(f'forecast_cache upsert: {n}건')
else:
    print('[WARN] BQ rows 없음')
" 2>&1 || warn "forecast BQ 동기화 실패 (계속 진행)"

  # 7-6. decision-svc plan-daily
  log "7-6 plan-daily 실행..."
  kubectl exec -n bookflow deploy/decision-svc -- python -c "
import httpx
r = httpx.post('http://localhost:80/decision/plan-daily', json={},
               headers={'Authorization': 'Bearer mock-token-hq-admin'}, timeout=60)
print(r.status_code, r.text[:200])
" 2>&1 || warn "plan-daily 실패 (계속 진행)"
else
  warn "7-5/7-6 ETL 미완료 — forecast·plan-daily 건너뜀"
fi

ELAPSED=$(( $(date +%s) - T0 ))
state_write "last-start-day" "$(date +%Y-%m-%dT%H:%M:%S)"
state_write "last-start-elapsed" "$ELAPSED"
echo ""
echo "═══ start-day done · ${ELAPSED}s · failed=${FAILED} ═══"
echo "  GCP VPN (수동): bash scripts/aws/ops/gcp-vpn-info.sh --full"
echo "  발표일 추가:    ./scripts/ops/network-mode.sh tgw && ./scripts/ops/eks-mode.sh private"
