#!/usr/bin/env bash
# etl.sh · Lambda (SAM) + Glue + Step Functions
# GCS_STAGING_BUCKET env var 설정 필요 (mart-to-gcs Lambda · raw_pos_mart → GCS → BigQuery):
#   export GCS_STAGING_BUCKET=<gcp-project-id>-bookflow-staging
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
ACTION="${1:-up}"
load_env; acquire_lock "etl"; init_log "etl" "$ACTION"; pre_flight
INFRA="$PROJECT_ROOT/infra/aws"

# GCS staging bucket for mart-to-gcs Lambda (raw_pos_mart Parquet → GCS → BigQuery)
GCP_PROJECT_ID="${GCP_PROJECT_ID:-}"
GCS_STAGING_BUCKET="${GCS_STAGING_BUCKET:-${GCP_PROJECT_ID:+${GCP_PROJECT_ID}-bookflow-staging}}"
export GCS_STAGING_BUCKET
[ -n "$GCS_STAGING_BUCKET" ] \
  && log "GCS staging bucket: gs://${GCS_STAGING_BUCKET}" \
  || warn "GCS_STAGING_BUCKET 미설정 — mart-to-gcs Lambda 비활성 (export GCS_STAGING_BUCKET=<bucket>)"

case "$ACTION" in
up)
  step "etl.sh up"
  # 직렬화 필수 — glue task 가 step-functions 만든 후 lambdas UPDATE (SF ARN 주입) 함.
  # lambdas CREATE 와 동시 실행 시 'Rollback requested by user' race.
  # start-day.sh 에서 step3(SAM 단독 배포) 완료 시 BOOKFLOW_LAMBDAS_DEPLOYED=1 이 export됨 → skip.
  # etl.sh 단독 실행 시에는 플래그 없으므로 정상 배포.
  if [[ "${BOOKFLOW_LAMBDAS_DEPLOYED:-}" == "1" ]]; then
    log "lambdas SAM 배포 skip (start-day.sh step3 에서 완료)"
  else
    step "1. lambdas (SAM) — CREATE 단독"
    py "$PROJECT_ROOT/scripts/aws/bookflow.py" task lambdas
  fi
  step "2. glue catalog + step-functions (+ lambdas UPDATE · SF ARN 주입)"
  py "$PROJECT_ROOT/scripts/aws/bookflow.py" task glue
  step "3. step-functions idempotent re-apply"
  cfn_deploy bookflow-99-step-functions "$INFRA/99-glue/step-functions.yaml"
  state_write "etl" "up"; step "etl.sh up done" ;;
down)
  step "etl.sh down · step-functions → glue → lambdas"
  cfn_bulk_delete "bookflow-99-step-functions" "bookflow-00-"
  py "$PROJECT_ROOT/scripts/aws/bookflow.py" task glue --down || true
  py "$PROJECT_ROOT/scripts/aws/bookflow.py" task lambdas --down || true
  cfn_bulk_delete "bookflow-99-" "bookflow-00-"
  state_write "etl" "down"; step "etl.sh down done" ;;
*) err "usage: $0 up|down"; exit 2 ;;
esac
