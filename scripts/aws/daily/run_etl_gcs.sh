#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  run_etl_gcs.sh · ETL 전체 파이프라인 실행 + GCS 적재 확인      ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  흐름:                                                       ║
# ║  1. glue-catalog + step-functions CFN 업데이트                ║
# ║  2. Glue 스크립트 4개 S3 업로드                                ║
# ║     (rds_inventory_mart · rds_locations_mart ·               ║
# ║      rds_store_location_map_mart · features_build)           ║
# ║  3. Step Functions 실행 (ETL1+ETL2 병렬 → ETL3)              ║
# ║     ParallelMart(7) → SalesDailyAgg → FeaturesBuild         ║
# ║  4. GCS mart/features/ 적재 확인                              ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  사전 조건:                                                   ║
# ║  - export GCS_BUCKET=<gcs-bucket-name>  (.env.local 권장)    ║
# ║  - inventory_snapshot_daily 에 오늘 날짜 스냅샷 존재            ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

# .env.local 자동 소싱 (GCS_BUCKET 등 로컬 변수)
ENV_LOCAL="${REPO_ROOT}/scripts/aws/config/.env.local"
if [ -f "${ENV_LOCAL}" ]; then
  set -a
  # shellcheck source=/dev/null
  source "${ENV_LOCAL}"
  set +a
fi

check_env

ACCOUNT=$(account_id)
GLUE_BUCKET="${PROJECT}-glue-scripts-${ACCOUNT}"
MART_BUCKET=$(stack_output "bookflow-00-s3" "MartBucketName" 2>/dev/null || \
              echo "${PROJECT}-mart-${ACCOUNT}")
GCS_BUCKET="${GCS_BUCKET:-${BOOKFLOW_GCS_BUCKET:-}}"

# BookFlowAI-Apps 경로 (REPO_ROOT = BookFlowAI-Platform)
APPS_ROOT="$(cd "${REPO_ROOT}/.." && pwd)/BookFlowAI-Apps"

# ── Glue Job 완료 대기 ───────────────────────────────────────────
wait_glue_job() {
  local job_name="$1"
  local run_id="$2"
  local timeout=1200  # 20분
  local elapsed=0
  local interval=30

  while [ $elapsed -lt $timeout ]; do
    local state
    state=$(aws glue get-job-run \
      --job-name "${job_name}" \
      --run-id "${run_id}" \
      --query 'JobRun.JobRunState' --output text 2>/dev/null || echo "UNKNOWN")

    case "${state}" in
      SUCCEEDED) ok "${job_name} 완료"; return 0 ;;
      FAILED|STOPPED|TIMEOUT|ERROR)
        aws glue get-job-run --job-name "${job_name}" --run-id "${run_id}" \
          --query 'JobRun.ErrorMessage' --output text 2>/dev/null | head -3
        err "${job_name} 실패: ${state}"
        ;;
      *)
        info "${job_name} 실행 중 ... (${state}, ${elapsed}s 경과)"
        sleep $interval
        elapsed=$((elapsed + interval))
        ;;
    esac
  done
  err "${job_name} timeout (${timeout}s 초과)"
}

# ── Step Functions 실행 완료 대기 ────────────────────────────────
wait_sfn() {
  local exec_arn="$1"
  local timeout=3600  # 1시간
  local elapsed=0
  local interval=30

  while [ $elapsed -lt $timeout ]; do
    local status
    status=$(aws stepfunctions describe-execution \
      --execution-arn "${exec_arn}" \
      --query 'status' --output text 2>/dev/null || echo "UNKNOWN")

    case "${status}" in
      SUCCEEDED) ok "Step Functions 완료"; return 0 ;;
      FAILED|ABORTED|TIMED_OUT)
        aws stepfunctions describe-execution \
          --execution-arn "${exec_arn}" \
          --query 'cause' --output text 2>/dev/null | head -3
        err "Step Functions 실패: ${status}"
        ;;
      *)
        info "Step Functions 실행 중 ... (${status}, ${elapsed}s 경과)"
        sleep $interval
        elapsed=$((elapsed + interval))
        ;;
    esac
  done
  err "Step Functions timeout (${timeout}s 초과)"
}

# ────────────────────────────────────────────────────────────────
# Step 1. CFN 업데이트 (glue-catalog + step-functions)
# ────────────────────────────────────────────────────────────────
step "Step 1 · CloudFormation 업데이트"

GCS_PARAM="${GCS_BUCKET:-}"
if [ -z "${GCS_PARAM}" ]; then
  warn "GCS_BUCKET 미설정 — features_build GCS dual-write 비활성"
fi

bookflow task glue
ok "bookflow-99-glue-catalog + bookflow-99-step-functions 업데이트 완료"

# ────────────────────────────────────────────────────────────────
# Step 2. Glue 스크립트 S3 업로드
# ────────────────────────────────────────────────────────────────
step "Step 2 · Glue 스크립트 S3 업로드"

if ! aws s3 ls "s3://${GLUE_BUCKET}/" > /dev/null 2>&1; then
  err "Glue scripts 버킷 접근 불가: s3://${GLUE_BUCKET}/"
fi

# ETL2 신규 스크립트 3개 (BookFlowAI-Apps)
for SCRIPT in rds_inventory_mart rds_locations_mart rds_store_location_map_mart; do
  # 디렉터리명은 언더스코어를 하이픈으로 (rds-inventory-mart/)
  DIR_NAME="${SCRIPT//_/-}"
  LOCAL="${APPS_ROOT}/glue-jobs/${DIR_NAME}/${SCRIPT}.py"
  if [ ! -f "${LOCAL}" ]; then
    err "${LOCAL} 없음 — Apps 레포 경로 확인"
  fi
  aws s3 cp "${LOCAL}" "s3://${GLUE_BUCKET}/scripts/${SCRIPT}.py" \
    --region "${REGION}" --no-progress
  ok "${SCRIPT}.py → s3://${GLUE_BUCKET}/scripts/"
done

# ETL3 features_build: Platform 버전(7테이블) 업로드
# Apps 버전(4테이블)이 있더라도 Platform 버전으로 덮어씀
FEATURES_SCRIPT="${REPO_ROOT}/glue-jobs/features_build.py"
if [ ! -f "${FEATURES_SCRIPT}" ]; then
  err "${FEATURES_SCRIPT} 없음"
fi
aws s3 cp "${FEATURES_SCRIPT}" "s3://${GLUE_BUCKET}/scripts/features_build.py" \
  --region "${REGION}" --no-progress
ok "features_build.py (Platform 7테이블 버전) → s3://${GLUE_BUCKET}/scripts/"

info "업로드된 스크립트 목록:"
aws s3 ls "s3://${GLUE_BUCKET}/scripts/" | grep "\.py$" | awk '{print "  · "$NF}'

# ────────────────────────────────────────────────────────────────
# Step 3. Step Functions 실행 (ETL1+ETL2 병렬 → ETL3)
# ────────────────────────────────────────────────────────────────
step "Step 3 · Step Functions 실행"

SFN_ARN=$(stack_output "bookflow-99-step-functions" "Etl3StateMachineArn" 2>/dev/null)
if [ -z "${SFN_ARN}" ]; then
  err "Step Functions ARN 조회 실패 — bookflow-99-step-functions 스택 확인"
fi
info "State Machine: ${SFN_ARN}"

EXEC_NAME="etl-gcs-$(date +%Y%m%d-%H%M%S)"
EXEC_ARN=$(aws stepfunctions start-execution \
  --state-machine-arn "${SFN_ARN}" \
  --name "${EXEC_NAME}" \
  --query 'executionArn' --output text)
ok "실행 시작: ${EXEC_NAME}"
info "ARN: ${EXEC_ARN}"
info "콘솔: https://ap-northeast-1.console.aws.amazon.com/states/home#/executions/details/${EXEC_ARN}"

wait_sfn "${EXEC_ARN}"

# ── 각 Job 결과 요약 ──────────────────────────────────────────────
info "Job 실행 결과:"
for JOB in raw-pos-mart raw-sns-mart raw-aladin-mart raw-event-mart \
           rds-inventory-mart rds-locations-mart rds-store-location-map-mart \
           sales-daily-agg features-build; do
  STATE=$(aws glue get-job-runs \
    --job-name "${PROJECT}-${JOB}" \
    --query 'JobRuns[0].JobRunState' \
    --max-results 1 --output text 2>/dev/null || echo "-")
  printf "  %-40s %s\n" "${PROJECT}-${JOB}" "${STATE}"
done

# ────────────────────────────────────────────────────────────────
# Step 4. S3 Mart 적재 확인
# ────────────────────────────────────────────────────────────────
step "Step 4 · S3 Mart 적재 확인"

for TABLE in sales_daily sns_mentions aladin_books calendar_events \
             inventory_daily locations_static store_location_map features; do
  CNT=$(aws s3 ls "s3://${MART_BUCKET}/mart/${TABLE}/" \
    --recursive 2>/dev/null | wc -l | tr -d ' \r')
  if [ "${CNT:-0}" -gt 0 ]; then
    ok "mart/${TABLE}/: ${CNT} 파일"
  else
    warn "mart/${TABLE}/: 파일 없음"
  fi
done

# ────────────────────────────────────────────────────────────────
# Step 5. GCS 적재 확인
# ────────────────────────────────────────────────────────────────
step "Step 5 · GCS 적재 확인"

if [ -z "${GCS_BUCKET}" ]; then
  warn "GCS_BUCKET 미설정 — GCS 확인 스킵"
  warn "확인하려면: export GCS_BUCKET=<bucket> 후 재실행"
else
  info "GCS_BUCKET: gs://${GCS_BUCKET}"

  if command -v gsutil > /dev/null 2>&1; then
    GCS_CNT=$(gsutil ls "gs://${GCS_BUCKET}/mart/features/" 2>/dev/null | wc -l | tr -d ' \r')
    if [ "${GCS_CNT:-0}" -gt 0 ]; then
      ok "gs://${GCS_BUCKET}/mart/features/: ${GCS_CNT} 파티션"
      info "최신 파티션:"
      gsutil ls "gs://${GCS_BUCKET}/mart/features/" 2>/dev/null | tail -5 | \
        while IFS= read -r line; do info "  ${line}"; done
    else
      warn "gs://${GCS_BUCKET}/mart/features/: 파일 없음 — features_build GCS_BUCKET 파라미터 확인"
    fi
  else
    warn "gsutil 미설치 — GCS 콘솔에서 직접 확인:"
    info "  gs://${GCS_BUCKET}/mart/features/"
  fi
fi

# ────────────────────────────────────────────────────────────────
step "완료"
cat << EOF
  체크리스트:
  [ ] bookflow-99-glue-catalog  업데이트
  [ ] bookflow-99-step-functions 업데이트
  [ ] rds_inventory_mart.py       S3 업로드
  [ ] rds_locations_mart.py       S3 업로드
  [ ] rds_store_location_map_mart.py S3 업로드
  [ ] features_build.py (Platform) S3 업로드
  [ ] Step Functions SUCCEEDED
  [ ] S3 mart/features/ 파일 존재
  [ ] GCS gs://${GCS_BUCKET:-<GCS_BUCKET>}/mart/features/ 파일 존재

  개별 Job 로그:
    aws glue get-job-runs --job-name bookflow-features-build --max-results 1
    aws logs tail /aws-glue/jobs/output --follow
EOF
