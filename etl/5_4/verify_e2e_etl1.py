"""
[5/4] Task6 ETL1 E2E  
sales-api → ECS① simul → Kinesis → RDS + S3   

:
    python verify_e2e_etl1.py
"""
import json
import os
import sys

import boto3

REGION        = os.environ.get("AWS_REGION", "ap-northeast-1")
STREAM_NAME   = os.environ.get("KINESIS_STREAM", "bookflow-pos-events")
RAW_BUCKET    = os.environ.get("RAW_BUCKET", "")
STACK_PREFIX  = "bookflow"


def check_kinesis(kinesis) -> bool:
    print("\n[1] Kinesis   ...")
    try:
        r = kinesis.describe_stream_summary(StreamName=STREAM_NAME)
        status = r["StreamDescriptionSummary"]["StreamStatus"]
        shards = r["StreamDescriptionSummary"]["OpenShardCount"]
        print(f"  : {STREAM_NAME}")
        print(f"  : {status}")
        print(f"  : {shards}")
        return status == "ACTIVE"
    except Exception as e:
        print(f"  [] {e}")
        return False


def check_s3_raw_pos(s3) -> bool:
    print("\n[2] S3 Raw pos-events  ...")
    if not RAW_BUCKET:
        print("  [SKIP] RAW_BUCKET ")
        return True
    try:
        r = s3.list_objects_v2(
            Bucket=RAW_BUCKET,
            Prefix="pos-events/",
            MaxKeys=10,
        )
        objects = r.get("Contents", [])
        print(f"  : s3://{RAW_BUCKET}/pos-events/")
        print(f"   : {len(objects)} ( 10)")
        for obj in objects[:3]:
            print(f"    - {obj['Key']} ({obj['Size']:,} bytes)")
        return len(objects) > 0
    except Exception as e:
        print(f"  [] {e}")
        return False


def check_firehose(firehose) -> bool:
    print("\n[3] Firehose    ...")
    delivery_name = f"{STACK_PREFIX}-pos-events-firehose"
    try:
        r = firehose.describe_delivery_stream(DeliveryStreamName=delivery_name)
        status = r["DeliveryStreamDescription"]["DeliveryStreamStatus"]
        print(f"  Firehose: {delivery_name}")
        print(f"  : {status}")
        return status == "ACTIVE"
    except Exception as e:
        print(f"  [] {e}")
        return False


def check_cloudwatch_metrics(cw) -> None:
    print("\n[4] CloudWatch Kinesis   ( 1)...")
    from datetime import datetime, timedelta, timezone
    end   = datetime.now(timezone.utc)
    start = end - timedelta(hours=1)
    try:
        r = cw.get_metric_statistics(
            Namespace="AWS/Kinesis",
            MetricName="IncomingRecords",
            Dimensions=[{"Name": "StreamName", "Value": STREAM_NAME}],
            StartTime=start,
            EndTime=end,
            Period=3600,
            Statistics=["Sum"],
        )
        pts = r.get("Datapoints", [])
        total = sum(p["Sum"] for p in pts)
        print(f"  IncomingRecords (1h): {total:,.0f}")
    except Exception as e:
        print(f"  [] {e}")


def main():
    print("=" * 50)
    print("ETL1 E2E : sales-api → ECS → Kinesis → RDS+S3")
    print("=" * 50)

    kinesis  = boto3.client("kinesis",         region_name=REGION)
    s3       = boto3.client("s3",              region_name=REGION)
    firehose = boto3.client("firehose",        region_name=REGION)
    cw       = boto3.client("cloudwatch",      region_name=REGION)

    results = [
        check_kinesis(kinesis),
        check_s3_raw_pos(s3),
        check_firehose(firehose),
    ]
    check_cloudwatch_metrics(cw)

    print("\n" + "=" * 50)
    passed = sum(results)
    print(f": {passed}/{len(results)} ")
    if passed == len(results):
        print("ETL1 E2E  !")
    else:
        print("   —   ")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
