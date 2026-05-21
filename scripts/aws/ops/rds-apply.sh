#!/usr/bin/env bash
# rds-apply.sh — RDS 스키마/시드/grants 를 ansible-node 경유로 수동 적용.
#   GHA rds-redeploy 의 OIDC/SSM 경로가 막혔을 때 로컬(deploy 프로파일)에서 동일 작업 수행.
#
# 사용:
#   AWS_PROFILE=bookflow-deploy AWS_REGION=ap-northeast-1 \
#     bash scripts/aws/ops/rds-apply.sh [BRANCH] [MODE]
#   BRANCH: 노드가 pull 할 git 브랜치 (기본 main · 머지 전이면 aws)
#   MODE  : schema | seed | grants | verify | full   (기본 schema)
#
# 예) migration 011 만 적용 (aws 브랜치, 머지 전):
#   AWS_PROFILE=bookflow-deploy AWS_REGION=ap-northeast-1 bash scripts/aws/ops/rds-apply.sh aws schema
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
BRANCH="${1:-main}"
MODE="${2:-schema}"

case "$MODE" in
  schema)  SEQ="rds-schema" ;;
  seed)    SEQ="rds-schema rds-seed" ;;
  grants)  SEQ="rds-grants" ;;
  verify)  SEQ="rds-verify" ;;
  full)    SEQ="rds-schema rds-seed rds-grants rds-verify" ;;
  *) echo "MODE 는 schema|seed|grants|verify|full" >&2; exit 2 ;;
esac

echo "== ansible-node 조회 =="
IID=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=bookflow-ansible-node" "Name=instance-state-name,Values=running" \
  --query "Reservations[0].Instances[0].InstanceId" --output text)
[ -z "$IID" ] || [ "$IID" = "None" ] && { echo "ansible-node(bookflow-ansible-node) 없음/정지" >&2; exit 1; }
echo "  instance: $IID · branch: $BRANCH · mode: $MODE ($SEQ)"

# 노드에서 실행할 스크립트: 브랜치 동기화 후 playbook 순차 실행.
# SSM 은 root(HOME 미설정)로 실행됨 → root 로 돌리되 HOME=/root 지정(git config --global 가능)
# + safe.directory(repo 는 ubuntu 소유라 root 가 만지면 dubious ownership) 둘 다 설정.
# root 는 /var/log/ansible 로그·repo 어디든 쓰기 가능하므로 ubuntu 전환보다 안전.
NODE_SCRIPT="set -e
export HOME=/root
git config --global --add safe.directory /opt/bookflow
cd /opt/bookflow
git fetch origin
git checkout $BRANCH
git pull origin $BRANCH"
for p in $SEQ; do
  NODE_SCRIPT="$NODE_SCRIPT
ansible-playbook cicd/ansible/playbooks/${p}.yml --roles-path cicd/ansible/roles"
done
# 작업 후 노드를 main 으로 복귀 (다음 GHA main 실행이 깨끗하도록 · best-effort)
[ "$BRANCH" != "main" ] && NODE_SCRIPT="$NODE_SCRIPT
git checkout main || true"

B64=$(printf '%s' "$NODE_SCRIPT" | base64 | tr -d '\n')

echo "== SSM send-command =="
CID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
  --document-name "AWS-RunShellScript" \
  --parameters commands="echo $B64 | base64 -d | bash" \
  --timeout-seconds 1800 \
  --query "Command.CommandId" --output text)
# fail-fast: 빈 CommandId 면 즉시 중단
[ -z "$CID" ] || [ "$CID" = "None" ] && { echo "send-command 실패 (CommandId 없음)" >&2; exit 1; }
echo "  CommandId: $CID · 대기 중..."

# 완료 대기 (최대 30분)
while true; do
  ST=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CID" --instance-id "$IID" --query "Status" --output text 2>/dev/null || echo "Pending")
  case "$ST" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac
  sleep 5
done

echo "== 결과: $ST =="
aws ssm get-command-invocation --region "$REGION" --command-id "$CID" --instance-id "$IID" \
  --query "StandardOutputContent" --output text | tail -40
if [ "$ST" != "Success" ]; then
  echo "== STDERR =="
  aws ssm get-command-invocation --region "$REGION" --command-id "$CID" --instance-id "$IID" \
    --query "StandardErrorContent" --output text | tail -30
  exit 1
fi
echo "== DONE =="
