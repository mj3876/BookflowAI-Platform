"""
spike-detect Lambda
10 cron ·  1 SNS   → Poisson Z-score ≥ 3.0 → RDS spike_events INSERT
VPC   (BookFlowAI VPC · RDS  )

[ETL2 급등 감지 파이프라인 전체 흐름]
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Step 2. 급등 감지 (EventBridge Cron)                                │
  │   EventBridge가 rate(10 minutes)로 이 Lambda를 강제 호출            │
  │   → sam-template.yaml Events.Cron.Schedule: 'rate(10 minutes)'     │
  └───────────┬─────────────────────────────────────────────────────────┘
              │
  ┌───────────▼─────────────────────────────────────────────────────────┐
  │ Step 3. S3 SNS 데이터 Z-score 분석                                   │
  │   sns-gen Lambda가 10분마다 S3 Raw/sns/에 적재한 데이터를 읽어서    │
  │   Poisson Z-score로 ISBN별 언급 급등 여부 판별 (임계값 Z ≥ 3.0)    │
  └───────────┬─────────────────────────────────────────────────────────┘
              │
  ┌───────────▼─────────────────────────────────────────────────────────┐
  │ Step 4. RDS spike_events INSERT (현재 구현 범위)                     │
  │   급등 도서를 RDS.spike_events 테이블에 기록                        │
  │                                                                     │
  │   [아키텍처 설계 vs 현재 구현 차이]                                 │
  │   아키텍처 도에는 spike-detect → Internal ALB → intervention-svc    │
  │   직접 HTTP 호출이 명시되어 있으나, 현재 이 코드에는 ALB 호출 없음  │
  │   intervention-svc(EKS Pod)는 spike_events 테이블을 폴링하거나      │
  │   별도 트리거 방식으로 연동 (bookflow-apps repo 참조)               │
  └─────────────────────────────────────────────────────────────────────┘
"""
import gzip
import json
import math
import os
from datetime import datetime, timedelta, timezone

import boto3
import psycopg2

REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
# Z-score 임계값: Poisson 분포에서 Z ≥ 3.0이면 통계적으로 유의미한 급등
# (정규분포 기준 상위 0.13% 수준 — 일반 노이즈와 실제 급등을 구분하는 기준선)
Z_THRESHOLD = 3.0


def _get_secret(sm, name: str) -> dict:
    return json.loads(sm.get_secret_value(SecretId=name)["SecretString"])


def _db_connect(secret: dict):
    # VPC 내부에서만 접근 가능한 RDS 엔드포인트
    # BookFlow AI VPC(10.0.0.0/16) → VPC Peering → Data VPC RDS(5432)
    # rds.yaml SecurityGroup이 10.0.0.0/16 인바운드 5432를 허용하여 연결 성립
    return psycopg2.connect(
        host=secret["host"],
        port=int(secret.get("port", 5432)),
        dbname=secret.get("dbname", "bookflow"),
        user=secret["username"],
        password=secret["password"],
        connect_timeout=10,
    )


def _read_sns_last_hour(s3, bucket: str, now: datetime) -> dict:
    """
    Step 3-① S3 SNS 데이터 수집
    sns-gen Lambda가 10분마다 S3 Raw/sns/year=.../month=.../day=.../hour=.../에 적재한
    gzip NDJSON 파일을 현재 시각 기준 최근 1~2시간치 읽어 isbn13별 언급 횟수 집계
    """
    counts: dict[str, int] = {}
    for delta in (0, 1):  # 현재 시간(delta=0)과 1시간 전(delta=1) 파티션 모두 스캔
        h = now - timedelta(hours=delta)
        # S3 Hive 파티션 경로: sns-gen이 적재할 때 동일 포맷으로 키를 생성함
        prefix = (
            f"sns/year={h.year}/month={h.month:02d}"
            f"/day={h.day:02d}/hour={h.hour:02d}/"
        )
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                try:
                    body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
                    for line in gzip.decompress(body).decode("utf-8").splitlines():
                        if not line.strip():
                            continue
                        rec = json.loads(line)
                        isbn13 = rec.get("isbn13", "")
                        if isbn13:
                            counts[isbn13] = counts.get(isbn13, 0) + 1
                except Exception as e:
                    print(f"[spike-detect] read error {obj['Key']}: {e}")
    return counts


def _z_score(count: int, lam: float) -> float:
    """
    Step 3-② Poisson Z-score 계산
    공식: Z = (실제 언급 수 - 기대 언급 수λ) / √λ
    λ(baseline_lam): Secrets Manager bookflow/sns-gen-config의 tracked_isbns[].baseline_lam
    평소 언급이 λ=5회인 책이 갑자기 20회 언급되면 Z = (20-5)/√5 ≈ 6.7 → 급등 판정
    """
    return (count - lam) / math.sqrt(lam) if lam > 0 else 0.0


def lambda_handler(event, context):
    # Step 2. EventBridge Cron이 10분마다 이 핸들러를 호출
    # event는 EventBridge 스케줄 이벤트 객체 (내용 사용 안 함, 트리거 신호만)
    sm         = boto3.client("secretsmanager", region_name=REGION)
    s3         = boto3.client("s3",             region_name=REGION)
    raw_bucket = os.environ["RAW_BUCKET"]

    # 추적 대상 ISBN 목록과 각 ISBN의 baseline_lam(기대 언급 횟수)을 Secrets Manager에서 로드
    # bookflow/sns-gen-config: {"tracked_isbns": [{"isbn13": "...", "baseline_lam": 5.0}, ...]}
    cfg      = _get_secret(sm, "bookflow/etl/sns-gen-config")
    tracked  = {b["isbn13"]: b for b in cfg.get("tracked_isbns", [])}
    rds_sec  = _get_secret(sm, "bookflow/rds/master-password")

    now    = datetime.now(timezone.utc)
    # Step 3-① S3에서 최근 1~2시간 SNS 언급 데이터 수집
    counts = _read_sns_last_hour(s3, raw_bucket, now)

    # Step 3-② 각 추적 ISBN에 대해 Z-score 계산 → 임계값 초과 시 spike 목록에 추가
    # schema: spike_events(event_id UUID PK, detected_at, isbn13, z_score NUMERIC(5,2),
    #                      mentions_count INT, triggered_order_id, resolved_at)
    import uuid as _uuid
    spikes = []
    for isbn13, book in tracked.items():
        lam   = float(book.get("baseline_lam", 5.0))
        count = counts.get(isbn13, 0)
        z     = _z_score(count, lam)
        if z >= Z_THRESHOLD:
            spikes.append({
                "event_id":       str(_uuid.uuid4()),
                "isbn13":         isbn13,
                "detected_at":    now.isoformat(),
                "z_score":        round(z, 2),  # NUMERIC(5,2)
                "mentions_count": count,
            })

    print(f"[spike-detect] {len(counts)} ISBNs · {len(spikes)} spikes (Z≥{Z_THRESHOLD})")

    if not spikes:
        return {"statusCode": 200, "spikes": 0}

    # Step 4. RDS spike_events INSERT
    # PK 가 event_id UUID 라 ON CONFLICT 는 event_id 기준 (사실상 발생 안 함 · 매 invocation 새 UUID)
    conn = _db_connect(rds_sec)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO spike_events
                        (event_id, detected_at, isbn13, z_score, mentions_count)
                    VALUES (%(event_id)s, %(detected_at)s, %(isbn13)s,
                            %(z_score)s, %(mentions_count)s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    spikes,
                )
    finally:
        conn.close()

    # [Step 5 연계: intervention-svc 자동 발주 로직 — bookflow-apps repo]
    # intervention-svc가 spike_events.is_resolved=False 레코드를 감지하면:
    #   1) 재고 데이터 기반으로 24시간 내 품절 예상 여부 판단
    #   2) 품절 예상 시 → RDS orders 테이블에 AUTO_EXECUTED 상태로 발주 기록
    #   3) 무승인 자동 발주 즉시 실행 후 대시보드(WebSocket 실시간 Push) 반영
    #   4) 처리 완료 후 spike_events.is_resolved = True 업데이트

    return {"statusCode": 200, "spikes": len(spikes)}
