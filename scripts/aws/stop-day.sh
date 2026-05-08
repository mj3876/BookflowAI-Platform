#!/usr/bin/env bash
# stop-day.sh · 18:00 매일 destroy (Tier 00 영구 보존)
# 흐름: 4 서비스 병렬 down → peering/cross-cloud → base
# 안전장치: 30s polling auto-retry · CFN export cascade · ELA Hyperplane ENI 자동 release 대기

set +e   # 일부 fail 해도 계속
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
load_env
init_log "stop-day" "down"
pre_flight

T0=$(date +%s)

step "1/3 4 서비스 병렬 down (eks · ecs · publisher · etl) + cicd"
"$SCRIPT_DIR/ops/eks.sh" down &
"$SCRIPT_DIR/ops/ecs.sh" down &
"$SCRIPT_DIR/ops/publisher.sh" down &
"$SCRIPT_DIR/ops/etl.sh" down &
"$SCRIPT_DIR/ops/cicd.sh" down &
wait

step "2/3 peering + cross-cloud down (둘 다 시도 · 어느 모드든 안전)"
"$SCRIPT_DIR/ops/peering.sh" down &
"$SCRIPT_DIR/ops/cross-cloud.sh" down &
wait

step "3/3 base down · 모든 자식 끝났으니 VPC 까지"
"$SCRIPT_DIR/ops/base.sh" down

ELAPSED=$(( $(date +%s) - T0 ))
state_write "last-stop-day" "$(date +%Y-%m-%dT%H:%M:%S)"
state_write "last-stop-elapsed" "$ELAPSED"
echo ""
echo "═══ stop-day done · ${ELAPSED}s · Tier 00 영구만 잔존 ═══"

# 잔존 검증
log "잔존 stack 검증:"
aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_FAILED \
  --query "StackSummaries[?starts_with(StackName,'bookflow-') && !starts_with(StackName,'bookflow-00-')].StackName" \
  --output text
