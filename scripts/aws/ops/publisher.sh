#!/usr/bin/env bash
# publisher.sh · 출판사 API (publisher-asg + alb-external · vpc-egress)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
ACTION="${1:-up}"
load_env; acquire_lock "publisher"; init_log "publisher" "$ACTION"; pre_flight
INFRA="$PROJECT_ROOT/infra/aws"

case "$ACTION" in
up)
  step "publisher.sh up · publisher-asg + alb-external (2 병렬)"
  cfn_parallel_deploy <<EOF
bookflow-40-publisher-asg|$INFRA/40-compute-runtime/publisher-asg.yaml
bookflow-50-alb-external|$INFRA/50-network-traffic/alb-external.yaml
EOF
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
