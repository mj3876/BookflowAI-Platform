"""gen_history_365 · D-7 ~ D-365 pending_orders history (약 17,950 rows).

generate.py 가 import 해서 사용. per_day=50 기준 358일 × 50 = 17,900 rows.
D-0 ~ D-6 는 gen_pending_orders_daily 가 처리하므로 여기서는 D-7 부터 생성.
"""
from __future__ import annotations

import random
import uuid
from datetime import timedelta

# generate.py 의 전역 상태를 import 해서 재사용
from generate import (
    NOW,
    PO_LEAD_DAYS,
    _po_row,
)


def gen_pending_orders_history_365(books, locations, per_day: int = 50) -> list[dict]:
    """D-7 ~ D-365 완료 주문 히스토리."""
    rows: list[dict] = []
    isbns = [b["isbn13"] for b in books]
    store_ids = [l["location_id"] for l in locations if l["location_id"] <= 12]
    wh_body_by_id = {
        l["wh_id"]: l["location_id"]
        for l in locations
        if l["location_type"] == "WH" and l.get("wh_id") is not None
    }
    wh_locs = [l["location_id"] for l in locations if l["location_type"] == "WH"]
    wh_groups = {1: [1, 2, 3, 4, 5, 6], 2: [7, 8, 9, 10, 11, 12]}

    for day_offset in range(7, 366):
        target_date = NOW.date() - timedelta(days=day_offset)
        for _ in range(per_day):
            order_type = random.choices(
                ["WH_TO_STORE", "REBALANCE", "WH_TRANSFER", "PUBLISHER_ORDER"],
                weights=[25, 35, 20, 20],
            )[0]
            urgency = random.choices(["NORMAL", "URGENT", "CRITICAL"], weights=[70, 25, 5])[0]
            auto_exec = urgency in ("URGENT", "CRITICAL")

            if auto_exec:
                status = random.choices(["AUTO_EXECUTED", "EXECUTED"], weights=[40, 60])[0]
            else:
                status = random.choices(["EXECUTED", "REJECTED"], weights=[55, 45])[0]

            rejection_stage = (
                random.choices(["PENDING", "APPROVED", "IN_TRANSIT"], weights=[50, 30, 20])[0]
                if status == "REJECTED" else None
            )

            isbn = random.choice(isbns[50:500])
            qty = random.randint(10, 80)
            target_dt = NOW.replace(
                year=target_date.year, month=target_date.month, day=target_date.day,
                hour=random.randint(9, 17), minute=random.randint(0, 59), second=0,
            )
            hours_ago = max(1, int((NOW - target_dt).total_seconds() / 3600))

            if order_type == "WH_TO_STORE":
                wh = random.choice([1, 2])
                src = wh_body_by_id.get(wh)
                if src is None:
                    continue
                tgt = random.choice(wh_groups[wh])
            elif order_type == "REBALANCE":
                wh = random.choice([1, 2])
                src, tgt = random.sample(wh_groups[wh], 2)
            elif order_type == "WH_TRANSFER":
                if len(wh_locs) < 2:
                    continue
                src, tgt = (wh_locs[0], wh_locs[1]) if random.random() < 0.5 else (wh_locs[1], wh_locs[0])
            else:  # PUBLISHER_ORDER
                src = None
                tgt = random.choice(wh_locs) if wh_locs else random.choice(store_ids)

            rows.append(_po_row(
                order_type, isbn, src, tgt, qty,
                urgency=urgency, status=status,
                auto_exec=auto_exec, hours_ago=hours_ago,
                reason="history_generated",
                rejection_stage=rejection_stage,
            ))
    return rows
