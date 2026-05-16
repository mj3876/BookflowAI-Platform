#!/usr/bin/env bash
# publisher.sh · 출판사 API (publisher-asg + alb-external · vpc-egress)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
ACTION="${1:-up}"
load_env; acquire_lock "publisher"; init_log "publisher" "$ACTION"; pre_flight

case "$ACTION" in
up)
  step "publisher.sh up · task publisher (alb-external → waf → publisher-asg → ecs-inventory-api 순차)"
  # publisher-asg 는 alb-external 의 TargetGroup ARN 을 파라미터로 받음 → 병렬 불가.
  # bookflow.py task publisher 가 ALB output 을 읽어 ASG/ECS 에 순차 전달.
  py "$PROJECT_ROOT/scripts/aws/bookflow.py" task publisher
  state_write "publisher" "up"; step "publisher.sh up done" ;;
down)
  step "publisher.sh down · CodeDeploy ASG 강제 삭제 + CFN delete"
  # CodeDeploy Blue-Green ASG 강제 삭제 (오늘 본 패턴)
  py - <<'PYEOF' || true
import boto3, os
session = boto3.Session(profile_name=os.environ['AWS_PROFILE'], region_name=os.environ['AWS_REGION'])
asg = session.client('autoscaling')
for g in asg.describe_auto_scaling_groups()['AutoScalingGroups']:
    if 'CodeDeploy_bookflow-publisher' in g['AutoScalingGroupName']:
        print(f"force delete ASG: {g['AutoScalingGroupName']}")
        try: asg.delete_auto_scaling_group(AutoScalingGroupName=g['AutoScalingGroupName'], ForceDelete=True)
        except Exception as e: print(f"  err: {e}")
PYEOF
  cfn_bulk_delete "bookflow-40-publisher-asg" "bookflow-00-"
  cfn_bulk_delete "bookflow-50-alb-external" "bookflow-00-"
  state_write "publisher" "down"; step "publisher.sh down done" ;;
*) err "usage: $0 up|down"; exit 2 ;;
esac
