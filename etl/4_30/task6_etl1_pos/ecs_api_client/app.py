"""
[4/30] Task6 ETL1 · ECS② ecs-api-client
   · sales-api(API Gateway)  →   + 
Egress VPC  · API Key 
"""
import json
import os
import random
import time
import uuid
from datetime import datetime, timezone

import requests

SALES_API_BASE = os.environ.get("SALES_API_BASE", "")
API_KEY        = os.environ.get("SALES_API_KEY", "")
REGION         = os.environ.get("AWS_REGION", "ap-northeast-1")
INTERVAL_MIN   = float(os.environ.get("INTERVAL_MIN", "15"))
INTERVAL_MAX   = float(os.environ.get("INTERVAL_MAX", "45"))

ISBNS = [
    "9788936434120", "9791165341909", "9788997253203", "9788932919126", "9788998441067",
    "9791162540138", "9788954657747", "9788950949372", "9788936433598", "9791190030205",
    "9788936472405", "9788937460449", "9788966261598", "9791164054312", "9788954647939",
]

PARTNER_IDS = ["partner-kyobo", "partner-yes24", "partner-interpark", "partner-coupang"]


def get_headers() -> dict:
    return {
        "x-api-key":    API_KEY,
        "Content-Type": "application/json",
        "x-partner-id": random.choice(PARTNER_IDS),
        "x-request-id": str(uuid.uuid4()),
    }


def check_availability(isbn13: str) -> dict | None:
    url = f"{SALES_API_BASE}/availability/{isbn13}"
    try:
        r = requests.get(url, headers=get_headers(), timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        print(f"[api-client] availability   {isbn13}: {e}", flush=True)
        return None


def check_stock(isbn13: str, location_id: int) -> dict | None:
    url = f"{SALES_API_BASE}/stock"
    params = {"isbn13": isbn13, "location_id": location_id}
    try:
        r = requests.get(url, headers=get_headers(), params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        print(f"[api-client] stock  : {e}", flush=True)
        return None


def get_catalog(isbn13: str) -> dict | None:
    url = f"{SALES_API_BASE}/catalog/{isbn13}"
    try:
        r = requests.get(url, headers=get_headers(), timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        print(f"[api-client] catalog   {isbn13}: {e}", flush=True)
        return None


def run_scenario() -> None:
    isbn13 = random.choice(ISBNS)
    scenario = random.choices(
        ["availability", "stock", "catalog"],
        weights=[40, 40, 20],
    )[0]
    ts = datetime.now(timezone.utc).isoformat()

    if scenario == "availability":
        result = check_availability(isbn13)
        status = "ok" if result else "fail"
        print(f"[api-client] {ts} AVAILABILITY isbn={isbn13} → {status}", flush=True)

    elif scenario == "stock":
        location_id = random.randint(1, 14)
        result = check_stock(isbn13, location_id)
        if result:
            avail = result.get("available", "?")
            print(
                f"[api-client] {ts} STOCK isbn={isbn13} loc={location_id} avail={avail}",
                flush=True,
            )
        else:
            print(f"[api-client] {ts} STOCK isbn={isbn13} → fail", flush=True)

    elif scenario == "catalog":
        result = get_catalog(isbn13)
        status = "ok" if result else "fail"
        print(f"[api-client] {ts} CATALOG isbn={isbn13} → {status}", flush=True)


def main() -> None:
    if not SALES_API_BASE:
        print("[api-client] SALES_API_BASE   —   ", flush=True)
        while True:
            isbn13 = random.choice(ISBNS)
            print(
                f"[api-client][DUMMY] {datetime.now(timezone.utc).isoformat()} "
                f"isbn={isbn13} scenario={random.choice(['availability','stock','catalog'])}",
                flush=True,
            )
            time.sleep(random.uniform(INTERVAL_MIN, INTERVAL_MAX))

    print(f"[api-client]  → base={SALES_API_BASE}", flush=True)
    while True:
        run_scenario()
        time.sleep(random.uniform(INTERVAL_MIN, INTERVAL_MAX))


if __name__ == "__main__":
    main()
