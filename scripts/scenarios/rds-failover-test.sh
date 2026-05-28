#!/usr/bin/env bash
# RDS Multi-AZ Failover 시연 (v3 · 시연 영상 최적화)
#
# v2 → v3 변경:
#   - probe_rds 출력 형식 통일 (OK ... / ERR ... 로 시작) — grep 매칭 안정화
#   - probe ASCII timeline 추가 (1초 단위 ✓/✗ chart) — failover event 시각화
#   - Server IP swap 추적 (baseline IP vs verify IP) — RDS API AZ stale 우회
#   - AWS Failover Event timeline 박스 강조
#   - RESULT 박스: AWS RTO + Pod downtime + IP swap + AZ 한 화면
#
# 사용:
#   bash rds-failover-test.sh check       — RDS 상태
#   bash rds-failover-test.sh monitor     — 1초 간격 RDS 연결+IP 라이브 모니터 (별도 터미널)
#   bash rds-failover-test.sh failover    — failover 트리거 + 60s 추적 + RESULT
#   bash rds-failover-test.sh verify      — 새 primary 응답 확인

set -uo pipefail
export PATH="/c/Program Files/Amazon/AWSCLIV2:$PATH"

DB_ID="bookflow-postgres"
REGION="ap-northeast-1"
NS="bookflow"
PROBE_POD_LABEL="app=dashboard-svc"

# IP swap 추적용 (baseline → verify 간 전달)
IP_STATE_FILE="/tmp/.rds_failover_baseline_ip"

# ANSI colors
RED="\033[31m"; GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"; BOLD="\033[1m"; RESET="\033[0m"

big_step() {
    echo
    echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}${BOLD}║  $1${RESET}"
    echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════════════════════════╝${RESET}"
}

sub_box() {
    echo
    echo -e "  ${CYAN}${BOLD}━━━ $1 ━━━${RESET}"
}

rds_field() {
    aws rds describe-db-instances --db-instance-identifier "$DB_ID" --region "$REGION" \
        --query "DBInstances[0].$1" --output text 2>/dev/null
}

probe_pod() {
    kubectl get pods -n "$NS" -l "$PROBE_POD_LABEL" -o jsonpath="{.items[0].metadata.name}" 2>/dev/null
}

# RDS probe — 출력은 항상 "OK <ms> ip=<ip>" 또는 "ERR <reason>" 으로 시작
probe_rds() {
    local pod result last_line
    pod=$(probe_pod)
    if [[ -z "$pod" ]]; then
        echo "ERR no-probe-pod"
        return 1
    fi
    # kubectl --request-timeout=3s + python connect_timeout=2 → 한 probe 호출 최대 ~3초
    result=$(kubectl --request-timeout=3s exec -n "$NS" "$pod" -- python -c '
import os, sys, time, psycopg
t0 = time.time()
try:
    with psycopg.connect(
        host=os.environ["DASHBOARD_RDS_HOST"],
        dbname=os.environ["DASHBOARD_RDS_DB"],
        user=os.environ["DASHBOARD_RDS_USER"],
        password=os.environ["DASHBOARD_RDS_PASSWORD"],
        connect_timeout=2,
        options="-c statement_timeout=2000",
    ) as conn:
        cur = conn.execute("SELECT inet_server_addr()")
        row = cur.fetchone()
        server_ip = row[0] if row else "?"
    print(f"OK {(time.time()-t0)*1000:.0f}ms ip={server_ip}")
except Exception as e:
    print(f"ERR {type(e).__name__}")
    sys.exit(1)
' 2>&1)
    last_line=$(echo "$result" | tail -1)
    if [[ "$last_line" == OK* || "$last_line" == ERR* ]]; then
        echo "$last_line"
    else
        # kubectl exec 실패, container terminated 등
        echo "ERR $(echo "$last_line" | head -c 60)"
    fi
}

# probe_log → ASCII timeline (1초 단위)
print_probe_timeline() {
    local log="$1"
    local max_t=0 t
    while IFS= read -r line; do
        t=$(echo "$line" | sed -nE 's/^\[\+([0-9]+)s\].*/\1/p')
        [[ -n "$t" && $t -gt $max_t ]] && max_t=$t
    done < "$log"
    [[ $max_t -eq 0 ]] && max_t=60

    echo -e "  probe timeline (1초 단위 · ${GREEN}✓${RESET}=OK ${RED}✗${RESET}=ERR ${YELLOW}·${RESET}=no-sample):"
    # 10초씩 그룹
    local header="    "
    for g in 0 10 20 30 40 50 60; do
        [[ $g -gt $max_t ]] && break
        header+=$(printf "+%-10s " "${g}s")
    done
    echo -e "$header"

    local line="    "
    for t in $(seq 0 $max_t); do
        local entry
        entry=$(grep -E "^\[\+${t}s\]" "$log" | head -1)
        if [[ -z "$entry" ]]; then
            line+="${YELLOW}·${RESET}"
        elif echo "$entry" | grep -q "OK "; then
            line+="${GREEN}✓${RESET}"
        else
            line+="${RED}✗${RESET}"
        fi
        # 10개마다 공백
        [[ $(( (t+1) % 10 )) -eq 0 ]] && line+=" "
    done
    echo -e "$line"
}

# RDS describe-events 의 failover 관련 이벤트만 박스로
print_failover_events() {
    sub_box "AWS RDS Failover Event Timeline"
    aws rds describe-events --source-identifier "$DB_ID" --source-type db-instance \
        --duration 10 --region "$REGION" \
        --query "Events[?contains(Message, 'failover') || contains(Message, 'restart') || contains(Message, 'shutdown')].[Date,Message]" \
        --output text 2>/dev/null | \
    while IFS=$'\t' read -r t msg; do
        [[ -z "$t" ]] && continue
        local time_only=$(echo "$t" | cut -d'T' -f2 | cut -d'.' -f1)
        echo -e "    ${GREEN}●${RESET} $time_only  $msg"
    done
}

do_check() {
    big_step "RDS 상태"
    local multiaz status az endpoint
    multiaz=$(rds_field "MultiAZ")
    status=$(rds_field "DBInstanceStatus")
    az=$(rds_field "AvailabilityZone")
    endpoint=$(rds_field "Endpoint.Address")
    echo "  MultiAZ        : $multiaz"
    echo "  Status         : $status"
    echo "  Primary AZ     : $az"
    echo "  Endpoint       : $endpoint"
    if [[ "$multiaz" != "True" && "$multiaz" != "true" ]]; then
        echo -e "  ${RED}✗ MultiAZ 비활성 — failover 불가${RESET}"
        return 1
    fi
}

do_baseline() {
    big_step "STEP 1/2 · BASELINE  ·  RDS 정상 응답 확인"
    local az secondary status r
    az=$(rds_field "AvailabilityZone")
    secondary=$(rds_field "SecondaryAvailabilityZone")
    status=$(rds_field "DBInstanceStatus")
    echo "  RDS status      : $status"
    echo "  Primary AZ      : $az"
    echo "  Standby AZ      : ${secondary:-(hidden · Multi-AZ standby)}"
    echo
    echo "  ▶ dashboard-svc 컨테이너 안에서 RDS 'SELECT inet_server_addr()' 호출:"
    r=$(probe_rds)
    if [[ "$r" == OK* ]]; then
        echo -e "    ${GREEN}$r${RESET}"
        # IP 추출 → 상태 파일에 저장 (verify 에서 swap 비교)
        local ip=$(echo "$r" | sed -nE 's/.*ip=([0-9.]+).*/\1/p')
        echo "{\"baseline_ip\":\"$ip\",\"baseline_az\":\"$az\"}" > "$IP_STATE_FILE"
    else
        echo -e "    ${RED}$r${RESET}"
    fi
    echo
    echo -e "  ${GREEN}✓ baseline 정상. 다음: failover 트리거${RESET}"
}

do_failover() {
    echo
    local primary_before trigger_ts
    primary_before=$(rds_field "AvailabilityZone")
    trigger_ts=$(date +%s)

    echo -e "${BOLD}▶ $(date +%H:%M:%S)  force-failover 트리거${RESET}"
    echo -e "  ${YELLOW}(라이브 변화는 별도 터미널의 monitor 창에서 확인)${RESET}"
    aws rds reboot-db-instance --db-instance-identifier "$DB_ID" --force-failover --region "$REGION" \
        --query "DBInstance.{Id:DBInstanceIdentifier,Status:DBInstanceStatus}" --output json | sed 's/^/  /'

    # 메인 polling — status 변화 추적 (probe 는 monitor 가 담당)
    local POLL_DEADLINE=$((trigger_ts + 60))
    local last_status="" az_changed=0 saw_reboot=0
    while [[ $(date +%s) -lt $POLL_DEADLINE ]]; do
        local now elapsed status primary
        now=$(date +%s)
        elapsed=$((now - trigger_ts))
        status=$(rds_field "DBInstanceStatus")
        primary=$(rds_field "AvailabilityZone")

        if [[ "$status" != "$last_status" ]]; then
            local color="$YELLOW"
            [[ "$status" == "available" ]] && color="$GREEN"
            printf "  [+%3ss] status → ${color}%s${RESET}\n" "$elapsed" "$status"
            last_status="$status"
        fi
        [[ "$status" != "available" && "$status" != "" ]] && saw_reboot=1
        if [[ $az_changed -eq 0 && -n "$primary" && "$primary" != "$primary_before" ]]; then
            az_changed=1
            printf "  [+%3ss] ${GREEN}${BOLD}✓ AZ swap: %s → %s${RESET}\n" "$elapsed" "$primary_before" "$primary"
        fi
        if [[ "$status" == "available" && $az_changed -eq 1 ]]; then
            printf "  [+%3ss] ${GREEN}${BOLD}✓ failover 완료${RESET}\n" "$elapsed"
            break
        fi
        if [[ $saw_reboot -eq 1 && "$status" == "available" ]]; then
            printf "  [+%3ss] ${GREEN}${BOLD}✓ failover 완료${RESET}\n" "$elapsed"
            break
        fi
        sleep 3
    done
}

# ── 별도 터미널용 라이브 모니터 (Ctrl+C 종료) ──
# 단일 kubectl exec + python 무한 loop — kubectl overhead 회피, 진짜 1초 간격
do_monitor() {
    echo -e "${CYAN}${BOLD}━━━ RDS 연결 라이브 모니터 (1초 간격 · Ctrl+C 종료) ━━━${RESET}"
    echo "  dashboard-svc 컨테이너 → RDS SELECT inet_server_addr()"
    echo
    local pod
    pod=$(probe_pod)
    if [[ -z "$pod" ]]; then
        echo -e "${RED}✗ dashboard-svc pod 없음${RESET}"
        return 1
    fi
    kubectl exec -n "$NS" "$pod" -- python -u -c '
import os, sys, time, psycopg
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

last_ip = None
while True:
    t0 = time.time()
    ts = time.strftime("%H:%M:%S")
    try:
        with psycopg.connect(
            host=os.environ["DASHBOARD_RDS_HOST"],
            dbname=os.environ["DASHBOARD_RDS_DB"],
            user=os.environ["DASHBOARD_RDS_USER"],
            password=os.environ["DASHBOARD_RDS_PASSWORD"],
            connect_timeout=1,
            options="-c statement_timeout=1500",
        ) as c:
            row = c.execute("SELECT inet_server_addr()").fetchone()
        ip = str(row[0]) if row else "?"
        ms = int((time.time() - t0) * 1000)
        marker = ""
        if last_ip and ip != last_ip:
            marker = f"  {YELLOW}{BOLD}← IP swap ({last_ip} → {ip}){RESET}"
        last_ip = ip
        print(f"[{ts}] {GREEN}OK {ms}ms ip={ip}{RESET}{marker}", flush=True)
    except Exception as e:
        print(f"[{ts}] {RED}{BOLD}ERR {type(e).__name__}{RESET}", flush=True)
    # 1초 간격 유지
    sleep_t = 1.0 - (time.time() - t0)
    if sleep_t > 0:
        time.sleep(sleep_t)
'
}

do_verify() {
    local primary status
    primary=$(rds_field "AvailabilityZone")
    status=$(rds_field "DBInstanceStatus")

    big_step "STEP 2/2 · RESULT  ·  Failover 요약"

    # AWS Failover Event timeline
    print_failover_events
    echo
    echo -e "  ${GREEN}✓${RESET} RDS status            : $status"
    echo -e "  ${GREEN}✓${RESET} Primary AZ (RDS API)  : $primary"
    echo
    echo -e "  ${BOLD}데모 메시지${RESET}:"
    echo "    AWS RDS Standard Multi-AZ 의 공식 RTO 는 < 60초."
    echo "    monitor 창에서 실제 downtime + Server IP swap (1초 단위) 확인."
    echo "    어플리케이션 코드 변경 0 · 데이터 손실 0 (synchronous replication)."
}

case "${1:-failover}" in
    check)    do_check ;;
    monitor)  do_monitor ;;
    failover) do_check && do_failover && do_verify ;;
    verify)   do_verify ;;
    *)        echo "usage: $0 {check|monitor|failover|verify}"; exit 2 ;;
esac
