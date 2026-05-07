"""
[4/30] Task6 ETL1 · ECS① offline-sim
() POS   → Kinesis bookflow-pos-events
: OFFLINE, location_id 3-14 ( 12)

: pos_etl.py(BookFlowAI-Apps)  
tx_id, isbn13, qty, unit_price, total_price, channel, location_id, ts
"""
import json
import os
import random
import time
import uuid
from datetime import datetime, timezone

import boto3

STREAM_NAME = os.environ.get("KINESIS_STREAM", "bookflow-pos-events")
REGION      = os.environ.get("AWS_REGION", "ap-northeast-1")
INTERVAL    = (30, 90)

ISBNS = [
    "9788936434120", "9791165341909", "9788997253203", "9788932919126", "9788998441067",
    "9791162540138", "9788954657747", "9788950949372", "9788936433598", "9791190030205",
    "9788936472405", "9788937460449", "9788966261598", "9791164054312", "9788954647939",
]

BRANCH_IDS = list(range(3, 15))
kinesis = boto3.client("kinesis", region_name=REGION)


def make_record() -> dict:
    isbn13     = random.choice(ISBNS)
    qty        = random.randint(1, 5)
    unit_price = random.randint(8_000, 35_000)
    return {
        "tx_id":       str(uuid.uuid4()),
        "isbn13":      isbn13,
        "qty":         qty,
        "unit_price":  unit_price,
        "total_price": qty * unit_price,
        "channel":     "OFFLINE",
        "location_id": random.choice(BRANCH_IDS),
        "ts":          datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    print(f"[offline-sim]  → stream={STREAM_NAME}", flush=True)
    while True:
        rec = make_record()
        try:
            kinesis.put_record(
                StreamName=STREAM_NAME,
                Data=json.dumps(rec, ensure_ascii=False).encode(),
                PartitionKey=rec["isbn13"],
            )
            print(
                f"[offline-sim] OFFLINE loc={rec['location_id']} isbn={rec['isbn13']} "
                f"qty={rec['qty']} total={rec['total_price']:,}",
                flush=True,
            )
        except Exception as e:
            print(f"[offline-sim] : {e}", flush=True)
        time.sleep(random.uniform(*INTERVAL))


if __name__ == "__main__":
    main()
