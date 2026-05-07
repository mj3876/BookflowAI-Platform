#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 08 · 5/7 ()  CI/CD glue-redeploy GHA            ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. Ansible Control Node  + SSM                  ║
# ║  2. glue-jobs/   → GitHub push → GHA      ║
# ║  3. SSM Send-Command → Ansible playbook              ║
# ║  4. S3 sync + Glue StartJobRun CI/CD                ║
# ║  :  (glue-redeploy.yml + glue-deploy.yml  )  ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)
GLUE_BUCKET="${PROJECT}-glue-scripts-${ACCOUNT}"

# ── Step 1. Ansible Control Node  ────────────────────────
step "Step 1 · Ansible Control Node  "

ANSIBLE_INSTANCE_ID=$(aws ec2 describe-instances \
  --filters \
    "Name=tag:Name,Values=${PROJECT}-ansible-node" \
    "Name=instance-state-name,Values=running" \
  --query "Reservations[0].Instances[0].InstanceId" \
  --output text --region "${REGION}" 2>/dev/null || echo "None")

if [ "${ANSIBLE_INSTANCE_ID}" = "None" ] || [ -z "${ANSIBLE_INSTANCE_ID}" ]; then
  warn "Ansible CN  ·  :"
  info "  python scripts/aws/bookflow.py base-up"
  info "  (  ansible-node  )"
  info ""
  info " : SSM   Ansible :"
  info "  pip install ansible amazon.aws"
  info "  ansible-playbook cicd/ansible/playbooks/glue-deploy.yml \\"
  info "    --inventory cicd/ansible/inventory/hosts.yml \\"
  info "    --roles-path cicd/ansible/roles \\"
  info "    -e github_sha=local-test"
else
  ok "Ansible CN: ${ANSIBLE_INSTANCE_ID}"

  # SSM  
  info "SSM  ..."
  CMD_ID=$(aws ssm send-command \
    --instance-ids "${ANSIBLE_INSTANCE_ID}" \
    --document-name "AWS-RunShellScript" \
    --parameters '{"commands":["echo SSM_OK && ansible --version | head -1"]}' \
    --query "Command.CommandId" \
    --output text --region "${REGION}" 2>/dev/null || echo "FAILED")

  if [ "${CMD_ID}" != "FAILED" ] && [ -n "${CMD_ID}" ]; then
    sleep 5
    RESULT=$(aws ssm get-command-invocation \
      --command-id "${CMD_ID}" \
      --instance-id "${ANSIBLE_INSTANCE_ID}" \
      --query 'StandardOutputContent' \
      --output text 2>/dev/null || echo "timeout")
    ok "SSM : ${RESULT}"
  else
    warn "SSM Send-Command  · IAM  "
  fi
fi

# ── Step 2. glue-redeploy.yml   ─────────────────────
step "Step 2 · glue-redeploy.yml  "

GHA_FILE="${REPO_ROOT}/.github/workflows/glue-redeploy.yml"
if [ -f "${GHA_FILE}" ]; then
  ok "glue-redeploy.yml "
  info "trigger paths:"
  grep -A2 "paths:" "${GHA_FILE}" | head -5 || true
  info "role-to-assume:"
  grep "role-to-assume" "${GHA_FILE}" | head -2 || true
else
  err "glue-redeploy.yml  · .github/workflows/ "
fi

# ── Step 3. glue-jobs/  → Push → GHA  ─────────────
step "Step 3 · CI/CD   (glue-jobs/  )"

cat << 'EOF'
  CI/CD   (Git Bash  ):

    # 1. glue-jobs/   touch ( )
    date > glue-jobs/.trigger
    git add glue-jobs/.trigger
    git commit -m "ci: glue-redeploy  "
    git push origin azure    # azure  push

    # ! GHA main  push  → main PR merge  
    #  azure : PR  → merge → GHA 

  GitHub Actions :
    https://github.com/MyosoonHwang/BookFlowAI-Platform/actions

   AWS SSM   (Ansible CN  ):
EOF

if [ "${ANSIBLE_INSTANCE_ID}" != "None" ] && [ -n "${ANSIBLE_INSTANCE_ID}" ]; then
  info "SSM glue-deploy.yml  ..."
  CMD_ID=$(aws ssm send-command \
    --instance-ids "${ANSIBLE_INSTANCE_ID}" \
    --document-name "AWS-RunShellScript" \
    --parameters "{\"commands\":[
      \"set -e\",
      \"cd /opt/bookflow && git pull origin main\",
      \"ansible-playbook cicd/ansible/playbooks/glue-deploy.yml --roles-path cicd/ansible/roles -e github_sha=day08-test\"
    ]}" \
    --timeout-seconds 600 \
    --region "${REGION}" \
    --query "Command.CommandId" \
    --output text 2>/dev/null || echo "FAILED")

  if [ "${CMD_ID}" != "FAILED" ]; then
    info "SSM Command ID: ${CMD_ID}"
    info " : aws ssm get-command-invocation --command-id ${CMD_ID} --instance-id ${ANSIBLE_INSTANCE_ID}"
    info "  (30)..."
    sleep 30
    aws ssm get-command-invocation \
      --command-id "${CMD_ID}" \
      --instance-id "${ANSIBLE_INSTANCE_ID}" \
      --query '{Status:Status,Stdout:StandardOutputContent}' \
      --output json 2>/dev/null | python3 -m json.tool | head -20
  fi
fi

# ── Step 4. S3 Glue scripts   ────────────────────────
step "Step 4 · S3 Glue    "

info "s3://${GLUE_BUCKET}/scripts/  :"
aws s3 ls "s3://${GLUE_BUCKET}/scripts/" 2>/dev/null | sort || warn " "

# ── Step 5. Glue Job    ──────────────────────────
step "Step 5 · Glue Job   "

for JOB in raw-pos-mart raw-sns-mart raw-aladin-mart raw-event-mart sales-daily-agg features-build; do
  LAST=$(aws glue get-job-runs \
    --job-name "${PROJECT}-${JOB}" \
    --query 'JobRuns[0].{State:JobRunState,Started:StartedOn}' \
    --output json 2>/dev/null | python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    print(f'    {r[\"State\"]} · {r[\"Started\"][:19]}')
except:
    print('     ')
")
  info "${JOB}: ${LAST}"
done

# ──    ──────────────────────────────────────
step "Day 08  "
cat << 'EOF'
  [ ] Ansible CN   ( CN  )
  [ ] SSM Send-Command  
  [ ] glue-redeploy.yml trigger paths 
  [ ] SSM  Ansible playbook   (CN  )
  [ ] S3 Glue scripts   
  [ ] 6 Glue Job   

  ★ GHA  main  push 
  ★ azure   → PR → merge  GHA 

(5/8)  : day09_0508_cicd_rds.sh
  → rds-redeploy GHA  +  E2E 
EOF
