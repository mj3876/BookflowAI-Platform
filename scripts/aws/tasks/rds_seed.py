"""task-rds-seed · Bundle SQL + CSV → S3 → SSM ansible-node → psql apply.

Replaces the prior placeholder. Idempotent: TRUNCATE + COPY + sequence reset.

Steps:
  1. tar -czf seed-bundle.tar.gz (cicd/ansible/sql/ + infra/aws/20-data-persistent/seed-data/)
  2. Upload to s3://bookflow-glue-scripts-{ACCOUNT}/seed/seed-bundle.tar.gz (ansible-node has read perm)
  3. SSM RunCommand on ansible-node:
        a. download + extract
        b. psql -f 001_tables.sql / 002_indexes.sql / 003_grants.sql
        c. \\copy each CSV (FK-respecting load order)
        d. SELECT setval(...) for SERIAL/BIGSERIAL sequences (post-seed must)
"""
from __future__ import annotations

import base64
import json
import subprocess
import tarfile
import time
from pathlib import Path

import boto3

from ..lib import Stack, log
from ..lib.config import Config

LOAD_ORDER = [
    "warehouses", "publishers", "authors", "books", "locations", "users",
    "inventory", "reservations", "forecast_cache", "pending_orders",
    "order_approvals", "returns", "new_book_requests", "notifications_log",
    "spike_events", "sales_realtime", "audit_log",
]
# kpi_daily 는 sales_realtime aggregation 으로 도출 (실 운영도 BQ kpi_daily_view sync).
# generate.py 의 kpi_daily.csv 는 무시 · REMOTE_SQL 의 aggregation 단계가 source.

REMOTE_SQL = """\
#!/bin/bash
set -e
SECRET_JSON=$(aws secretsmanager get-secret-value --secret-id bookflow/rds/master-password --region {region} --query SecretString --output text)
export PGPASSWORD=$(echo "$SECRET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")
USER=$(echo "$SECRET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['username'])")
export PGSSLMODE=require
H={rds_host}
PSQL="psql -h $H -U $USER -d bookflow -v ON_ERROR_STOP=1"

cd /tmp
rm -rf seed && mkdir seed && cd seed
aws s3 cp s3://{bucket}/seed/seed-bundle.tar.gz . --region {region}
tar -xzf seed-bundle.tar.gz

echo "=== DDL + Indexes + Grants ==="
$PSQL -f cicd/ansible/sql/001_tables.sql > /tmp/ddl.log 2>&1 || {{ tail -50 /tmp/ddl.log; exit 1; }}
$PSQL -f cicd/ansible/sql/002_indexes.sql >> /tmp/ddl.log 2>&1
$PSQL -f cicd/ansible/sql/003_grants.sql >> /tmp/ddl.log 2>&1

echo "=== Migrations (002~006 in sorted order) ==="
for m in cicd/ansible/sql/migrations/*.sql; do
  [ -f "$m" ] || continue
  echo "  apply $m"
  $PSQL -f "$m" >> /tmp/ddl.log 2>&1 || {{ echo "  FAIL $m"; tail -50 /tmp/ddl.log; exit 1; }}
done

echo "=== TRUNCATE + COPY ==="
$PSQL -c "TRUNCATE TABLE {truncate_list} RESTART IDENTITY CASCADE"
SEED_DIR=infra/aws/20-data-persistent/seed-data
for t in {load_order}; do
  csv="$SEED_DIR/${{t}}.csv"
  [ -f "$csv" ] || {{ echo "  skip $t"; continue; }}
  # column-aware \copy: csv header → DB column list (DB 추가 컬럼은 default · generate.py csv 가 source of truth)
  COLS=$(head -1 "$csv")
  $PSQL -At -c "\\copy $t ($COLS) FROM '$csv' WITH (FORMAT csv, HEADER true);" > /dev/null
  n=$($PSQL -At -c "SELECT count(*) FROM $t")
  echo "  $t -> $n"
done

echo "=== Sequence reset (post-seed required) ==="
$PSQL -c "
SELECT setval('authors_author_id_seq',       COALESCE((SELECT MAX(author_id)    FROM authors),    1), true);
SELECT setval('publishers_publisher_id_seq', COALESCE((SELECT MAX(publisher_id) FROM publishers), 1), true);
SELECT setval('audit_log_log_id_seq',        COALESCE((SELECT MAX(log_id)       FROM audit_log),  1), true);
SELECT setval('new_book_requests_id_seq',    COALESCE((SELECT MAX(id)           FROM new_book_requests), 1), true);
"

echo "=== Aggregate kpi_daily from sales_realtime (sales_realtime 이 단일 truth source) ==="
$PSQL -c "
TRUNCATE TABLE kpi_daily;
WITH agg AS (
    SELECT (event_ts AT TIME ZONE 'Asia/Seoul')::date AS kpi_date,
           store_id, channel,
           SUM(qty)::int                    AS qty_sold,
           SUM(revenue)::bigint             AS revenue,
           COUNT(*)::int                    AS tx_count,
           COUNT(DISTINCT isbn13)::int      AS unique_isbn,
           (SUM(revenue) / NULLIF(SUM(qty),0))::int AS avg_price
      FROM sales_realtime
     GROUP BY 1, 2, 3
),
top_per AS (
    SELECT (event_ts AT TIME ZONE 'Asia/Seoul')::date AS kpi_date,
           store_id, channel, isbn13,
           SUM(qty) AS q,
           ROW_NUMBER() OVER (
               PARTITION BY (event_ts AT TIME ZONE 'Asia/Seoul')::date, store_id, channel
               ORDER BY SUM(qty) DESC
           ) AS rn
      FROM sales_realtime
     GROUP BY 1, 2, 3, isbn13
)
INSERT INTO kpi_daily (kpi_date, store_id, category_id, channel, qty_sold, revenue, tx_count, avg_price, unique_isbn_count, top_isbn, synced_from_bq_at)
SELECT a.kpi_date, a.store_id, 0, a.channel, a.qty_sold, a.revenue, a.tx_count, a.avg_price, a.unique_isbn, t.isbn13, NOW()
  FROM agg a
  LEFT JOIN top_per t
    ON t.kpi_date = a.kpi_date AND t.store_id = a.store_id AND t.channel = a.channel AND t.rn = 1;
"
n_kpi=$($PSQL -At -c "SELECT count(*) FROM kpi_daily")
echo \"  kpi_daily (aggregated from sales_realtime) -> $n_kpi\"

echo \"=== ALL DONE ===\"
"""


def _bundle_to_s3(bucket: str, region: str) -> None:
    repo_root = Config.REPO_ROOT
    bundle = Path("/tmp/seed-bundle.tar.gz")
    bundle.parent.mkdir(parents=True, exist_ok=True)
    log.step("Building seed-bundle.tar.gz")
    with tarfile.open(bundle, "w:gz") as tf:
        tf.add(repo_root / "cicd" / "ansible" / "sql", arcname="cicd/ansible/sql")
        tf.add(repo_root / "infra" / "aws" / "20-data-persistent" / "seed-data",
               arcname="infra/aws/20-data-persistent/seed-data")
    s3 = boto3.client("s3", region_name=region)
    s3.upload_file(str(bundle), bucket, "seed/seed-bundle.tar.gz")
    log.success(f"  uploaded s3://{bucket}/seed/seed-bundle.tar.gz")


def _ssm_run(instance_id: str, script: str, region: str) -> None:
    ssm = boto3.client("ssm", region_name=region)
    cmd = (
        f"echo {base64.b64encode(script.encode()).decode()} | base64 -d > /tmp/seed.sh "
        f"&& bash /tmp/seed.sh"
    )
    log.step(f"SSM send-command → {instance_id}")
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [cmd]},
        TimeoutSeconds=900,
    )
    cmd_id = resp["Command"]["CommandId"]
    log.info(f"  CommandId: {cmd_id} · polling...")
    time.sleep(5)
    for _ in range(60):
        try:
            r = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(5); continue
        status = r.get("Status")
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            log.info(r.get("StandardOutputContent", "")[-2000:])
            if status != "Success":
                log.err(r.get("StandardErrorContent", "")[-1500:])
                raise SystemExit(1)
            log.success("  seed apply complete")
            return
        time.sleep(15)
    raise SystemExit("SSM polling timeout")


def deploy() -> None:
    log.step("=== task-rds-seed · DDL + grants + CSV copy + sequence reset ===")

    rds = Stack(tier="20", name="rds", template="")
    if not rds.exists():
        log.err("RDS not deployed · run `task data` first"); raise SystemExit(1)
    ans = Stack(tier="30", name="ansible-node", template="")
    if not ans.exists():
        log.err("ansible-node not deployed · check Tier 30"); raise SystemExit(1)

    # peering 모드 (default) 에선 ansible-data peering 이 seed 라우팅 (10.4→10.3) 필수.
    # TGW 모드 (Phase 4) 에선 TGW 가 라우팅 → peering 잔재 정리. tier 60 tgw stack 존재로 모드 감지.
    tgw_active = Stack(tier="60", name="tgw", template="").exists()
    leftover = Stack(tier="10", name="peering-ansible-data", template="")
    if leftover.exists() and tgw_active:
        log.info("  peering-ansible-data leftover · destroy (TGW 모드 정합)")
        leftover.destroy()

    rds_host = rds.outputs().get("DbEndpointAddress")
    instance_id = ans.outputs().get("InstanceId")
    bucket = f"{Config.PROJECT_NAME}-glue-scripts-{Config.account_id()}"
    log.info(f"  rds={rds_host} · ansible={instance_id} · bucket={bucket}")

    _bundle_to_s3(bucket, Config.REGION)
    truncate_list = ", ".join(reversed(LOAD_ORDER))
    script = REMOTE_SQL.format(
        region=Config.REGION,
        rds_host=rds_host,
        bucket=bucket,
        truncate_list=truncate_list,
        load_order=" ".join(LOAD_ORDER),
    )
    _ssm_run(instance_id, script, Config.REGION)
    log.step("=== task-rds-seed complete ===")


def destroy() -> None:
    log.step("=== task-rds-seed-down · noop (peering 모드는 ops/peering.sh 가 cleanup) ===")
    # TGW 모드 잔재만 정리. peering 모드는 ops/peering.sh down 이 5 peering 일괄 정리.
    tgw_active = Stack(tier="60", name="tgw", template="").exists()
    leftover = Stack(tier="10", name="peering-ansible-data", template="")
    if leftover.exists() and tgw_active:
        leftover.destroy()
    log.step("=== task-rds-seed-down complete ===")
