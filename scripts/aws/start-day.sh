#!/usr/bin/env bash
# start-day.sh · 09:00 매일 deploy
# 흐름: base → peering → 4 서비스 병렬 → seed
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

step "1/4 base · prereq"
"$SCRIPT_DIR/ops/base.sh" up

step "2/4 peering · cross-VPC 통신"
"$SCRIPT_DIR/ops/peering.sh" up

step "3/4 4 서비스 병렬 (eks · ecs · publisher · etl)"
"$SCRIPT_DIR/ops/eks.sh" up &        EKS_PID=$!
"$SCRIPT_DIR/ops/ecs.sh" up &        ECS_PID=$!
"$SCRIPT_DIR/ops/publisher.sh" up &  PUB_PID=$!
"$SCRIPT_DIR/ops/etl.sh" up &        ETL_PID=$!

FAILED=0
for pid in $EKS_PID $ECS_PID $PUB_PID $ETL_PID; do
  if ! wait $pid; then FAILED=$((FAILED+1)); fi
done
[ $FAILED -gt 0 ] && err "$FAILED service failed (logs/ 확인)"

step "4/5 seed · parquet → RDS (003_grants.sql · 11 pod role 생성)"
"$SCRIPT_DIR/ops/seed.sh" up

# 5단계: seed 후 eks-addons 의 _sync_rds_pod_roles 재호출 (role password 정합 + 7 pod restart)
# 4단계 병렬에서 eks-addons 가 seed 보다 먼저 끝나 ALTER ROLE fail 한 경우 자동 정정.
step "5/5 eks-addons resync · ALTER ROLE 11 + 7 pod rollout (DB pool 정합)"
py "$PROJECT_ROOT/scripts/aws/bookflow.py" task eks-addons || warn "eks-addons resync 실패 (이미 정합이면 무시)"

ELAPSED=$(( $(date +%s) - T0 ))
state_write "last-start-day" "$(date +%Y-%m-%dT%H:%M:%S)"
state_write "last-start-elapsed" "$ELAPSED"
echo ""
echo "═══ start-day done · ${ELAPSED}s · failed=${FAILED} ═══"
echo "  발표일 추가: ./scripts/ops/network-mode.sh tgw && ./scripts/ops/eks-mode.sh private"
