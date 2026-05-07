"""
[5/8] Task7 ETL2 E2E 
aladin-sync / event-sync / sns-gen / spike-detect cron  + S3 Raw  

:
    python verify_e2e_etl2.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import boto3

REGION     = os.environ.get("AWS_REGION", "ap-northeast-1")
RAW_BUCKET = os.environ.get("RAW_BUCKET", "")


def check_s3_prefix(s3, prefix: str, label: str) -> bool:
    print(f"\n  [{label}] s3://{RAW_BUCKET}/{prefix}")
    if not RAW_BUCKET:
        print("    [SKIP] RAW_BUCKET ")
        return True
    try:
        r = s3.list_objects_v2(Bucket=RAW_BUCKET, Prefix=prefix, MaxKeys=5)
        objs = r.get("Contents", [])
        if objs:
            for o in objs[:3]:
                print(f"    {o['Key']} ({o['Size']:,}B, {o['LastModified'].strftime('%Y-%m-%d %H:%M')})")
            return True
        print("     ")
        return False
    except Exception as e:
        print(f"    [] {e}")
        return False


def check_lambda_recent(lam, func_name: str) -> bool:
    print(f"\n  Lambda: {func_name}")
    try:
        r = lam.get_function(FunctionName=func_name)
        state = r["Configuration"]["State"]
        modified = r["Configuration"]["LastModified"]
        print(f"    : {state} | : {modified}")
        return state == "Active"
    except Exception as e:
        print(f"    [] {e}")
        return False


def main():
    now = datetime.now(timezone.utc)
    today = f"year={now.year}/month={now.month:02d}/day={now.day:02d}"
    yesterday = now - timedelta(days=1)
    yday = f"year={yesterday.year}/month={yesterday.month:02d}/day={yesterday.day:02d}"

    s3  = boto3.client("s3",     region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)

    print("=" * 55)
    print("ETL2 E2E :    ")
    print("=" * 55)

    print("\n[1] S3 Raw   ")
    s3_results = [
        check_s3_prefix(s3, f"aladin/{today}/",   "aladin-sync ()"),
        check_s3_prefix(s3, f"aladin/{yday}/",    "aladin-sync ()"),
        check_s3_prefix(s3, f"events/{today}/",   "event-sync ()"),
        check_s3_prefix(s3, f"sns/{today}/",      "sns-gen ()"),
    ]

    print("\n[2] Lambda  ")
    func_names = [
        "bookflow-aladin-sync",
        "bookflow-event-sync",
        "bookflow-sns-gen",
        "bookflow-spike-detect",
    ]
    lam_results = [check_lambda_recent(lam, fn) for fn in func_names]

    all_results = s3_results + lam_results
    passed = sum(all_results)

    print("\n" + "=" * 55)
    print(f": {passed}/{len(all_results)} ")
    if passed == len(all_results):
        print("ETL2 E2E  !")
    else:
        print("   —    ")
    return 0 if passed == len(all_results) else 1


if __name__ == "__main__":
    sys.exit(main())
