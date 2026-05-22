#!/usr/bin/env bash
# start-day.sh · 매일 실행
# 흐름: base → peering → SAM 재배포 → 4 서비스 병렬 → seed → eks-addons resync → 데이터 파이프라인
# 데이터 파이프라인 (6단계):
#   aladin-sync Lambda → event-sync Lambda → ETL Step Functions 트리거
#   → Step Functions 완료 대기 → forecast-svc BQ 동기화 → decision-svc plan-daily
# 발표일: + ./scripts/ops/network-mode.sh tgw + ./scripts/ops/eks-mode.sh private
#
# Env:
#   BOOKFLOW_ENV=admin|deploy (default deploy)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
load_env
init_log "start-day" "up"
pre_flight

T0=$(date +%s)

step "1/7 base · prereq"
"$SCRIPT_DIR/ops/base.sh" up

step "2/7 network · TGW 활성이면 cross-cloud 유지 · 아니면 peering"
TGW_ACTIVE=$(AWS_PROFILE="${AWS_PROFILE:-bookflow-deploy}" AWS_REGION="${AWS_REGION:-ap-northeast-1}" \
  aws cloudformation describe-stacks --stack-name bookflow-60-tgw \
  --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "NONE")
if [[ "$TGW_ACTIVE" == *"COMPLETE"* ]]; then
  log "TGW 모드 감지 — cross-cloud.sh 상태 유지 (peering.sh skip)"
else
  "$SCRIPT_DIR/ops/peering.sh" up
fi

# SAM 먼저 단독 배포 — etl.sh 병렬 실행 전 sam-template.yaml 변경사항을 확정.
# etl.sh 내부의 lambdas 단계는 BOOKFLOW_LAMBDAS_DEPLOYED=1 이면 skip.
step "3/7 SAM (99-serverless) · sam-template.yaml 반영 배포"
py "$PROJECT_ROOT/scripts/aws/bookflow.py" task lambdas
export BOOKFLOW_LAMBDAS_DEPLOYED=1

step "4/7 4 서비스 병렬 (eks · ecs · publisher · etl)"
"$SCRIPT_DIR/ops/eks.sh" up &        EKS_PID=$!
"$SCRIPT_DIR/ops/ecs.sh" up &        ECS_PID=$!
"$SCRIPT_DIR/ops/publisher.sh" up &  PUB_PID=$!
"$SCRIPT_DIR/ops/etl.sh" up &        ETL_PID=$!

FAILED=0
for pid in $EKS_PID $ECS_PID $PUB_PID $ETL_PID; do
  if ! wait $pid; then FAILED=$((FAILED+1)); fi
done
if [ $FAILED -gt 0 ]; then
  err "$FAILED service(s) failed — logs/ 확인 후 개별 스크립트로 재시도"
  exit 1
fi

step "5/7 seed · parquet → RDS (003_grants.sql · 11 pod role 생성)"
"$SCRIPT_DIR/ops/seed.sh" up

# 6단계: seed 후 eks-addons 의 _sync_rds_pod_roles 재호출 (role password 정합 + 7 pod restart)
# 4단계 병렬에서 eks-addons 가 seed 보다 먼저 끝나 ALTER ROLE fail 한 경우 자동 정정.
step "6/7 eks-addons resync · ALTER ROLE 11 + 7 pod rollout (DB pool 정합)"
py "$PROJECT_ROOT/scripts/aws/bookflow.py" task eks-addons || warn "eks-addons resync 실패 (이미 정합이면 무시)"

step "7/7 데이터 파이프라인 (aladin-sync → event-sync → ETL → forecast → plan-daily)"

AWS_REGION="${AWS_REGION:-ap-northeast-1}"

# 6-1. aladin-sync Lambda 수동 실행
log "6-1 aladin-sync Lambda 실행..."
aws lambda invoke --function-name bookflow-aladin-sync \
  --invocation-type RequestResponse \
  --payload '{}' /tmp/aladin-sync-out.json \
  --region "$AWS_REGION" --output json \
  --query "StatusCode" 2>&1 | grep -v "^$" || warn "aladin-sync 실패 (계속 진행)"

# 6-2. event-sync Lambda 수동 실행
log "6-2 event-sync Lambda 실행..."
aws lambda invoke --function-name bookflow-event-sync \
  --invocation-type RequestResponse \
  --payload '{}' /tmp/event-sync-out.json \
  --region "$AWS_REGION" --output json \
  --query "StatusCode" 2>&1 | grep -v "^$" || warn "event-sync 실패 (계속 진행)"

# 6-3. ETL Step Functions 실행
SF_ARN=$(aws cloudformation list-exports \
  --query "Exports[?Name=='bookflow-sfn-etl3-arn'].Value" \
  --output text --region "$AWS_REGION" 2>/dev/null || echo "")

if [[ -z "$SF_ARN" ]]; then
  warn "Step Functions ARN 조회 실패 — ETL 건너뜀"
else
  log "6-3 ETL Step Functions 시작: ${SF_ARN}"
  EXEC_ARN=$(aws stepfunctions start-execution \
    --state-machine-arn "$SF_ARN" \
    --input '{"trigger":"start-day"}' \
    --query "executionArn" --output text \
    --region "$AWS_REGION" 2>&1) || { warn "Step Functions 시작 실패 (계속 진행)"; EXEC_ARN=""; }

  # 6-4. Step Functions 완료 대기 (최대 30분)
  if [[ -n "$EXEC_ARN" ]]; then
    log "6-4 ETL 완료 대기 (최대 30분)..."
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

# 6-5. forecast-svc BQ 동기화 (ETL 성공 시)
if [[ -n "${EXEC_ARN:-}" ]]; then
  log "6-5 forecast-svc BQ 동기화..."
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

  # 6-6. decision-svc plan-daily
  log "6-6 plan-daily 실행..."
  kubectl exec -n bookflow deploy/decision-svc -- python -c "
import httpx
r = httpx.post('http://localhost:80/decision/plan-daily', json={},
               headers={'Authorization': 'Bearer mock-token-hq-admin'}, timeout=60)
print(r.status_code, r.text[:200])
" 2>&1 || warn "plan-daily 실패 (계속 진행)"
else
  warn "6-5/6-6 ETL 미완료 — forecast·plan-daily 건너뜀"
fi

ELAPSED=$(( $(date +%s) - T0 ))
state_write "last-start-day" "$(date +%Y-%m-%dT%H:%M:%S)"
state_write "last-start-elapsed" "$ELAPSED"
echo ""
echo "═══ start-day done · ${ELAPSED}s · failed=${FAILED} ═══"
echo "  발표일 추가: ./scripts/ops/network-mode.sh tgw && ./scripts/ops/eks-mode.sh private"
