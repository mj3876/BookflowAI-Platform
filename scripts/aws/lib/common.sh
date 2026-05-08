#!/usr/bin/env bash
# Common helpers · 모든 ops/*.sh 가 source.
# usage: source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

set -euo pipefail

# ── paths ──
# scripts/aws/lib/common.sh  → SCRIPTS_DIR=scripts/aws, PROJECT_ROOT=repo root
# Git Bash 의 POSIX path (/c/Users/...) 는 Windows native Python/AWS CLI 가 못 읽음.
# cygpath 가 있으면 Windows path 로 정규화 (mixed slash OK).
_winpath() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else echo "$1"; fi
}
_lib_posix="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(_winpath "$(cd "$_lib_posix/.." && pwd)")"
PROJECT_ROOT="$(_winpath "$(cd "$_lib_posix/../../.." && pwd)")"
LOG_DIR="$(_winpath "$(cd "$_lib_posix/../../.." && pwd)/logs")"
STATE_FILE="$HOME/.bookflow/state.json"
mkdir -p "$(cd "$_lib_posix/../../.." && pwd)/logs" "$(dirname "$STATE_FILE")"

# ── env (profile · account · region) ──
load_env() {
  local env_name="${BOOKFLOW_ENV:-deploy}"   # default deploy · admin 으로 override
  local env_file="$SCRIPTS_DIR/config/${env_name}.env"
  [ -f "$env_file" ] || { echo "ERR: $env_file 없음" >&2; exit 1; }
  set -a; . "$env_file"; set +a
  export AWS_PROFILE="${AWS_PROFILE:-bookflow-${env_name}}"
  export AWS_REGION="${AWS_REGION:-ap-northeast-1}"
}

# ── logging ──
log() {
  echo "[$(date '+%H:%M:%S')] $*"
}
step() {
  echo ""
  echo "═══ $* ═══"
}
warn() { log "WARN: $*" >&2; }
err()  { log "ERR:  $*" >&2; }

# ── tee 로그 + lock 동시 실행 방지 ──
acquire_lock() {
  local svc="$1"
  if command -v flock >/dev/null 2>&1; then
    exec 200>"/tmp/bookflow-${svc}.lock"
    flock -n 200 || { err "이미 ${svc} 진행 중 (다른 셸)"; exit 1; }
  else
    # Windows Git Bash 등 flock 부재 환경 — lock 건너뜀 (동시 실행 책임은 운영자)
    warn "flock 부재 — ${svc} lock skip"
  fi
}

# ── tee 로그 시작 ──
init_log() {
  local svc="$1" mode="${2:-up}"
  local log_file="$LOG_DIR/$(date +%Y-%m-%d)_${svc}_${mode}.log"
  exec > >(tee -a "$log_file") 2>&1
  log "log: $log_file"
}

# ── pre-flight: tool 설치 + sts identity ──
pre_flight() {
  local need_kubectl="${1:-no}"
  for t in aws; do
    command -v "$t" >/dev/null || { err "$t 미설치"; exit 1; }
  done
  if [ "$need_kubectl" = "yes" ]; then
    for t in kubectl helm; do
      command -v "$t" >/dev/null || { err "$t 미설치"; exit 1; }
    done
  fi
  local who; who=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null) \
    || { err "AWS 인증 실패 (profile=$AWS_PROFILE)"; exit 1; }
  log "AWS: $who · region=$AWS_REGION"
}

# ── CFN delete + auto-retry (boto3 통해 Python 실행) ──
cfn_bulk_delete() {
  local prefix="$1"   # 예: 'bookflow-' or 'bookflow-40-'
  local exclude_prefix="${2:-bookflow-00-}"   # 영구 보존
  py - <<PYEOF
import boto3, time, sys, os
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
cf = boto3.Session(profile_name=os.environ['AWS_PROFILE'],
                   region_name=os.environ['AWS_REGION']).client('cloudformation')
def active():
    r = cf.list_stacks(StackStatusFilter=[
        'CREATE_COMPLETE','UPDATE_COMPLETE','UPDATE_ROLLBACK_COMPLETE',
        'DELETE_FAILED','DELETE_IN_PROGRESS'])
    return [s for s in r['StackSummaries']
            if s['StackName'].startswith('$prefix')
            and not s['StackName'].startswith('$exclude_prefix')]
for i in range(120):
    rows = active()
    if not rows: print(f'[{i*30}s] DONE'); break
    in_p = [s['StackName'] for s in rows if s['StackStatus']=='DELETE_IN_PROGRESS']
    todo = [s['StackName'] for s in rows if s['StackStatus']!='DELETE_IN_PROGRESS']
    for n in todo:
        try: cf.delete_stack(StackName=n)
        except Exception as e: pass
    print(f'[{i*30}s] in_progress={len(in_p)} retry={len(todo)}')
    if todo: print(f'  retry: {todo[:5]}')
    time.sleep(30)
PYEOF
}

# ── CFN deploy single stack (idempotent) ──
cfn_deploy() {
  local stack="$1" template="$2"; shift 2
  local params=()
  while [ $# -gt 0 ]; do
    params+=("$1"); shift
  done
  log "deploy $stack"
  if [ ${#params[@]} -gt 0 ]; then
    aws cloudformation deploy --stack-name "$stack" --template-file "$template" \
      --parameter-overrides "${params[@]}" \
      --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
      --no-fail-on-empty-changeset
  else
    aws cloudformation deploy --stack-name "$stack" --template-file "$template" \
      --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
      --no-fail-on-empty-changeset
  fi
}

# ── parallel deploy (boto3) ──
cfn_parallel_deploy() {
  # input: stack-name|template-path[|param=val…] · 한 줄당 한 stack · stdin
  # stdin 을 임시파일로 모아서 Python 이 파일에서 읽기 (heredoc 충돌 회피)
  local spec_file
  spec_file=$(mktemp -t bookflow-specs-XXXXXX.txt)
  cat > "$spec_file"
  if [ ! -s "$spec_file" ]; then
    err "cfn_parallel_deploy: stdin 비어있음 (호출자 heredoc 확인)"
    rm -f "$spec_file"; return 1
  fi
  log "  parallel specs: $(wc -l <"$spec_file") stack(s)"
  CFN_SPEC_FILE="$(_winpath "$spec_file")" py - <<'PYEOF'
import boto3, sys, os, concurrent.futures
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)
session = boto3.Session(profile_name=os.environ['AWS_PROFILE'], region_name=os.environ['AWS_REGION'])
cf = session.client('cloudformation')
with open(os.environ['CFN_SPEC_FILE'], encoding='utf-8') as f:
    specs = [l.strip().split('|') for l in f if l.strip()]
def deploy(spec):
    name, tpl = spec[0], spec[1]
    params = spec[2:] if len(spec) > 2 else []
    print(f"  deploy {name}", flush=True)
    with open(tpl, encoding='utf-8') as f: body = f.read()
    kwargs = dict(StackName=name, TemplateBody=body,
                  Capabilities=['CAPABILITY_NAMED_IAM','CAPABILITY_AUTO_EXPAND'])
    if params:
        kwargs['Parameters'] = [
            {'ParameterKey': p.split('=',1)[0], 'ParameterValue': p.split('=',1)[1]}
            for p in params
        ]
    try:
        cf.update_stack(**kwargs)
        action = 'update'
    except cf.exceptions.ClientError as e:
        if 'does not exist' in str(e):
            cf.create_stack(**kwargs); action = 'create'
        elif 'No updates' in str(e): return f"  - {name} (no changes)"
        else: return f"  ✗ {name}: {str(e)[:120]}"
    waiter = cf.get_waiter(f'stack_{action}_complete')
    try:
        waiter.wait(StackName=name, WaiterConfig={'Delay':15,'MaxAttempts':80})
        return f"  ✓ {name}"
    except Exception as e:
        return f"  ✗ {name}: {str(e)[:120]}"
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
    for r in ex.map(deploy, specs): print(r, flush=True)
PYEOF
  local rc=$?
  rm -f "$spec_file"
  return $rc
}

# ── orphan NLB/ALB 강제 정리 (K8s LoadBalancer Controller 가 만든 LB 는 CFN external) ──
# CFN-managed LB 는 'aws:cloudformation:stack-name' tag 로 식별 후 skip.
cleanup_orphan_lbs() {
  log "orphan LB 검사 (bookflow-* tagged VPC 전체)"
  local vpcs total=0
  vpcs=$(aws ec2 describe-vpcs \
    --filters "Name=tag:Name,Values=bookflow-*" \
    --query "Vpcs[].VpcId" --output text 2>/dev/null)
  [ -z "$vpcs" ] && { log "  bookflow VPC 없음 — skip"; return 0; }
  for vpc in $vpcs; do
    local arns
    arns=$(aws elbv2 describe-load-balancers \
      --query "LoadBalancers[?VpcId=='${vpc}'].LoadBalancerArn" --output text 2>/dev/null)
    [ -z "$arns" ] && continue
    for arn in $arns; do
      local cfn_tag
      cfn_tag=$(aws elbv2 describe-tags --resource-arns "$arn" \
        --query "TagDescriptions[0].Tags[?Key=='aws:cloudformation:stack-name'].Value" \
        --output text 2>/dev/null)
      if [ -n "$cfn_tag" ] && [ "$cfn_tag" != "None" ]; then
        log "  skip CFN: $arn ($cfn_tag)"; continue
      fi
      log "  orphan delete: $arn"
      local tgs
      tgs=$(aws elbv2 describe-target-groups --load-balancer-arn "$arn" \
        --query "TargetGroups[].TargetGroupArn" --output text 2>/dev/null)
      aws elbv2 delete-load-balancer --load-balancer-arn "$arn" 2>&1 || true
      for tg in $tgs; do
        aws elbv2 delete-target-group --target-group-arn "$tg" 2>&1 || true
      done
      total=$((total+1))
    done
  done
  log "  cleanup: $total orphan LB"
  [ $total -gt 0 ] && { log "  ENI release 60s 대기"; sleep 60; }
  return 0
}

# ── state ──
state_write() {
  local key="$1" val="$2"
  py -c "
import json, os
sf = os.path.expanduser('~/.bookflow/state.json')
os.makedirs(os.path.dirname(sf), exist_ok=True)
d = json.load(open(sf, encoding='utf-8')) if os.path.exists(sf) else {}
d['$key'] = '$val'
json.dump(d, open(sf, 'w', encoding='utf-8'), indent=2)
"
}
state_read() {
  local key="$1"
  py -c "
import json, os
sf = os.path.expanduser('~/.bookflow/state.json')
d = json.load(open(sf, encoding='utf-8')) if os.path.exists(sf) else {}
print(d.get('$key', ''))
"
}
