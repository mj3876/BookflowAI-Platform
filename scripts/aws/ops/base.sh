#!/usr/bin/env bash
# base.sh · 모든 서비스 공통 prereq.
# Wave 1 (6 병렬): 5 VPC + ecs-cluster
# Wave 2 (8 병렬): 3 endpoints + ansible-node + rds + redis + kinesis + nat-gateway + route53

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

ACTION="${1:-up}"

load_env
acquire_lock "base"
init_log "base" "$ACTION"
pre_flight

INFRA="$PROJECT_ROOT/infra/aws"

case "$ACTION" in
up)
  step "base.sh up"

  step "Wave 1 · 5 VPC + ecs-cluster (6 병렬)"
  cfn_parallel_deploy <<EOF
bookflow-10-vpc-bookflow-ai|$INFRA/10-network-core/vpc-bookflow-ai.yaml
bookflow-10-vpc-sales-data|$INFRA/10-network-core/vpc-sales-data.yaml
bookflow-10-vpc-egress|$INFRA/10-network-core/vpc-egress.yaml
bookflow-10-vpc-data|$INFRA/10-network-core/vpc-data.yaml
bookflow-10-vpc-ansible|$INFRA/10-network-core/vpc-ansible.yaml
bookflow-30-ecs-cluster|$INFRA/30-compute-cluster/ecs-cluster.yaml
EOF

  step "Wave 2 · endpoints + data + ansible + nat + route53 (8 병렬)"
  cfn_parallel_deploy <<EOF
bookflow-10-endpoints-bookflow-ai|$INFRA/10-network-core/endpoints/endpoints-bookflow-ai.yaml
bookflow-10-endpoints-sales-data|$INFRA/10-network-core/endpoints/endpoints-sales-data.yaml
bookflow-10-endpoints-ansible|$INFRA/10-network-core/endpoints/endpoints-ansible.yaml
bookflow-30-ansible-node|$INFRA/30-compute-cluster/ansible-node.yaml
bookflow-20-rds|$INFRA/20-data-persistent/rds.yaml
bookflow-20-redis|$INFRA/20-data-persistent/redis.yaml
bookflow-20-kinesis|$INFRA/20-data-persistent/kinesis.yaml
bookflow-50-nat-gateway|$INFRA/50-network-traffic/nat-gateway.yaml
bookflow-10-route53|$INFRA/10-network-core/route53.yaml
EOF

  state_write "base" "up"
  step "base.sh up done"
  ;;
down)
  step "base.sh down · 모든 자식 stack 끝나야 (eks/ecs/publisher/etl/seed/peering 먼저 down)"

  # 안전망: orphan NLB/ALB (K8s controller 가 만든 LB) 강제 정리 → VPC subnet 의존 해소
  cleanup_orphan_lbs

  cfn_bulk_delete "bookflow-10-" "bookflow-00-"
  cfn_bulk_delete "bookflow-20-" "bookflow-00-"
  cfn_bulk_delete "bookflow-30-ecs-cluster" "bookflow-00-"
  cfn_bulk_delete "bookflow-30-ansible-node" "bookflow-00-"
  cfn_bulk_delete "bookflow-50-" "bookflow-00-"
  state_write "base" "down"
  step "base.sh down done"
  ;;
*)
  err "usage: $0 up|down"
  exit 2
  ;;
esac
