# BookFlow AI 시연 Walkthrough

V6.2 / FR-A6 의 5가지 핵심 시나리오를 RDS 시드 reset → 발의 → 승인/거부 → 입고 → 최종 계획안 검증까지 단계별로 따라간다. 각 단계마다 SSM session 으로 RDS 에 붙어 검증 SQL 을 실행하여 status / inventory / audit 정합을 확인한다.

본 문서는 **deploy 계정 (354493396671)** 기준이다. admin 계정에서는 동일 흐름이 가능하나 GitHub OIDC 가 없어 CI/CD 트리거 단계가 빠진다.

---

## 0. 준비

### 0.1 시드 reset

```bash
# Platform 레포 root
./scripts/aws/seed-reset.sh           # rds 시드 재적재 (1000책 × 12 store × 2 wh)
./scripts/aws/run-publisher-sim.sh    # 출판사 시뮬 ECS Fargate task 기동 (재고조회)
```

reset 후 RDS 시드 분포:
- `books` 1000 row · 알라딘 OpenAPI 실 호출
- `inventory` 1000 × 12 store + 1000 × 2 wh
- `forecast_cache` 1000 × 14 = 14000 (Vertex AI 결과 14일치)
- `pending_orders` 0 (당일 발의 전)

### 0.2 plan-daily 발의 (decision-svc)

```bash
# kubectl port-forward 또는 internal ALB host
TOKEN="Bearer mock-token-hq-admin"
curl -sX POST http://decision-svc/decision/plan-daily \
  -H "Authorization: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"snapshot_date":"2026-05-14","dry_run":false}'
```

결과:
- 부족 매장 × 책 cascade → REBALANCE / WH_TRANSFER / PUBLISHER_ORDER pending row 생성
- 응답: `{snapshot_date, total_orders, by_stage:{REBALANCE,WH_TRANSFER,PUBLISHER_ORDER}}`

검증 SQL:
```sql
SELECT order_type, COUNT(*), SUM(qty)
  FROM pending_orders
 WHERE forecast_rationale->>'plan_snapshot_date' = '2026-05-14'
 GROUP BY order_type;
```

---

## 1. 시나리오 A — REBALANCE 양측 협의 흐름 (branch-clerk)

같은 권역 (wh1) 안의 store A (강남점 store_3) → store B (잠실점 store_5) 재배치.
2026-05-14 정정: REBALANCE 도 WH_TRANSFER 와 동일하게 SOURCE/TARGET 양측 승인 필요.

### 1.1 plan-daily 발의 후 REBALANCE 확인

```sql
SELECT order_id, isbn13, source_location_id, target_location_id, qty, status
  FROM pending_orders
 WHERE order_type = 'REBALANCE'
   AND source_location_id = 3   -- 강남점
   AND target_location_id = 5   -- 잠실점
   AND status = 'PENDING'
 ORDER BY created_at DESC LIMIT 5;
```

### 1.2 SOURCE 측 (강남점 branch3) 승인

`branch3@...` 로그인 → BranchInbound → 출고 대기 탭에 해당 REBALANCE row → "✓ 승인" 클릭.
side 는 frontend 가 source_location_id == scope_store_id 로 자동 추론.

API:
```
POST /intervention/approve
{ "order_id": "<oid>", "approval_side": "SOURCE" }
```

검증 (status PENDING 유지 · TARGET 미승인 · UX "✓ 내 측 처리 끝 · 상대 측 대기"):
```sql
SELECT po.status, oa.approval_side, oa.decision
  FROM pending_orders po
  JOIN order_approvals oa USING (order_id)
 WHERE po.order_id = '<oid>'
 ORDER BY oa.decided_at;
-- status = PENDING · 1 row (SOURCE/APPROVED)
SELECT on_hand FROM inventory WHERE isbn13='<isbn>' AND location_id=3;
-- 미차감 (양측 모두 승인 전)
```

### 1.3 TARGET 측 (잠실점 branch5) 승인

`branch5@...` 로그인 → BranchInbound → 입고 대기 탭에 같은 row → "✓ 승인" 클릭.
side 는 target_location_id == scope_store_id 로 자동 추론.

API:
```
POST /intervention/approve
{ "order_id": "<oid>", "approval_side": "TARGET" }
```

검증 (status=APPROVED + inventory[강남점] -qty + audit_log):
```sql
SELECT status, approved_at FROM pending_orders WHERE order_id = '<oid>';
-- status = APPROVED
SELECT on_hand FROM inventory WHERE isbn13='<isbn>' AND location_id=3;
-- 강남점 -qty (TARGET 시점에 한 번만)
SELECT entity_id, after_state
  FROM audit_log
 WHERE entity_id = '<isbn>:3'
   AND action = 'inventory.adjust'
 ORDER BY created_at DESC LIMIT 1;
-- after_state.delta = -qty · reason="양측 승인 출고:..."
```

### 1.4 TARGET 매장 입고 수령

`branch5@...` 로그인 → 입고 list → "수령" 클릭.

API:
```
POST /intervention/inbound/<oid>/receive
```

검증:
```sql
SELECT status, executed_at FROM pending_orders WHERE order_id = '<oid>';
-- status = EXECUTED
SELECT on_hand FROM inventory WHERE isbn13='<isbn>' AND location_id=5;
-- 잠실점 +qty (방금 inventory-svc /adjust 가 처리)
```

---

## 2. 시나리오 B — WH_TRANSFER 양측 협의 (wh1 + wh2)

권역간 (wh1 ↔ wh2) 이동은 SOURCE 측과 TARGET 측이 각각 승인해야 status='APPROVED' 로 전환된다.

### 2.1 SOURCE 측 (wh-manager-1) 승인

```
POST /intervention/approve
{ "order_id": "<oid>", "approval_side": "SOURCE" }
```

검증 (status 는 아직 PENDING · UX 상 "TARGET 승인 대기" 배지):
```sql
SELECT po.status, oa.approval_side, oa.decision, oa.approver_role
  FROM pending_orders po
  JOIN order_approvals oa USING (order_id)
 WHERE po.order_id = '<oid>'
 ORDER BY oa.decided_at;
-- status = PENDING · 1 row (SOURCE/APPROVED)
SELECT on_hand FROM inventory WHERE isbn13='<isbn>' AND location_id='<source_loc>';
-- 미차감 (양측 모두 승인 전엔 변동 없음)
```

### 2.2 TARGET 측 (wh-manager-2) 승인

```
POST /intervention/approve
{ "order_id": "<oid>", "approval_side": "TARGET" }
```

검증 (status='APPROVED' + source -qty 1번만):
```sql
SELECT status, approved_at FROM pending_orders WHERE order_id = '<oid>';
-- status = APPROVED
SELECT COUNT(*) FROM audit_log
 WHERE entity_id = '<isbn>:<source_loc>'
   AND after_state->>'order_id' = '<oid>'
   AND action = 'inventory.adjust';
-- 1 row (TARGET 시점에만 차감)
```

### 2.3 TARGET 입고 수령

wh-manager-2 로그인 → 입고 list → 수령.

```sql
SELECT status, executed_at FROM pending_orders WHERE order_id = '<oid>';
-- EXECUTED
SELECT on_hand FROM inventory WHERE isbn13='<isbn>' AND location_id='<target_wh_loc>';
-- +qty
```

---

## 3. 시나리오 C — 거부 흐름

세 가지 거부 시점이 있다. 각 시점별로 inventory 동작이 다르다.

### 3.1 PENDING 상태 reject

발의된 REBALANCE 를 SOURCE/TARGET 어느 한쪽이 거부하면 전체 REJECTED:

```
POST /intervention/reject
{ "order_id": "<oid>", "approval_side": "SOURCE", "reject_reason": "중복 발의" }
```

검증 (status REJECTED · inventory 변동 없음):
```sql
SELECT status, reject_reason, reject_count FROM pending_orders WHERE order_id='<oid>';
-- REJECTED · reject_count = 1
SELECT COUNT(*) FROM audit_log
 WHERE after_state->>'order_id' = '<oid>'
   AND action = 'inventory.adjust';
-- 0 (한 번도 차감 안 됐으니 복원도 없음)
```

### 3.2 APPROVED 후 /reject (승인 취소)

APPROVED 인 REBALANCE/WH_TRANSFER 를 사후 거부 → source 복원.

```sql
-- 사전: 시나리오 A.1.2 + 1.3 후 (REBALANCE 양측 APPROVED)
SELECT on_hand FROM inventory WHERE isbn13='<isbn>' AND location_id='<source>';
-- 가령 90 (100 - 10)
```

```
POST /intervention/reject
{ "order_id": "<oid>", "approval_side": "TARGET", "reject_reason": "취소 요청" }
```

검증 (status REJECTED + source +qty 복원):
```sql
SELECT status, reject_reason FROM pending_orders WHERE order_id='<oid>';
-- REJECTED
SELECT on_hand FROM inventory WHERE isbn13='<isbn>' AND location_id='<source>';
-- 100 (복원)
SELECT after_state->>'delta' AS delta, after_state->>'reason' AS reason
  FROM audit_log
 WHERE after_state->>'order_id' = '<oid>'
   AND action = 'inventory.adjust'
 ORDER BY created_at;
-- 2 row: delta=-10 (REBALANCE 출고) → delta=+10 (거부복원)
```

### 3.3 APPROVED 후 /inbound/reject (입고 거부)

배송된 책의 수량 불일치/파손/누락 시 매장 또는 WH 가 입고를 거부. source 가 복원되고 후속 재발주가 가능해진다.

```
POST /intervention/inbound/<oid>/reject
{ "reject_reason": "10권 중 3권 파손" }
```

검증:
```sql
SELECT status, reject_reason FROM pending_orders WHERE order_id='<oid>';
-- REJECTED
SELECT on_hand FROM inventory WHERE isbn13='<isbn>' AND location_id='<source>';
-- +qty 복원
SELECT after_state->>'reason'
  FROM audit_log
 WHERE after_state->>'order_id'='<oid>' AND action='inventory.adjust'
 ORDER BY created_at DESC LIMIT 1;
-- "입고거부 복원:<oid_prefix>"
```

---

## 4. 시나리오 D — hq-admin 일괄 승인 (PUBLISHER_ORDER strict)

hq-admin 은 단건 escalation 으로는 어느 stage 든 가능하지만, 일괄 승인 (`/intervene/approve-all-today`) 에선 strict 권한 적용 → PUBLISHER_ORDER 만 승인.

UI: hq-admin 으로 로그인 → Approval 페이지 상단 "본사 강제 승인" 버튼 클릭.

```
POST /intervention/intervene/approve-all-today
{}   # order_type filter 없음
```

검증 (PUBLISHER_ORDER PENDING 만 APPROVED):
```sql
SELECT order_type, status, COUNT(*)
  FROM pending_orders
 WHERE forecast_rationale->>'plan_snapshot_date' = '2026-05-14'
 GROUP BY order_type, status
 ORDER BY order_type, status;
-- PUBLISHER_ORDER 의 PENDING → APPROVED 로 이동
-- REBALANCE / WH_TRANSFER 는 그대로 (wh-manager 영역)
SELECT COUNT(*) FROM audit_log
 WHERE action = 'intervention.batch_approved'
   AND after_state->>'approver_role' = 'hq-admin';
-- 1 row · sample_orders 가 PUBLISHER_ORDER 만 포함
```

---

## 5. 시나리오 E — 최종 계획안 (3-tab) 검증

decision-svc 의 `GET /decision/plan-daily/{snapshot_date}/summary` + `/items` 가 dashboard 의 "전체 계획안 · 승인 진행 · 실행 결과" 3 tab 데이터 소스다.

### 5.1 summary 조회 (탭 전환)

```bash
curl -s "http://decision-svc/decision/plan-daily/2026-05-14/summary" \
  -H "Authorization: $TOKEN" | jq
```

응답:
```json
{
  "snapshot_date": "2026-05-14",
  "by_stage_status": [
    {"order_type":"REBALANCE","status":"PENDING","cnt":120,"qty_total":1450},
    {"order_type":"REBALANCE","status":"APPROVED","cnt":5,"qty_total":62},
    {"order_type":"WH_TRANSFER","status":"PENDING","cnt":12,"qty_total":380},
    {"order_type":"PUBLISHER_ORDER","status":"APPROVED","cnt":7,"qty_total":910}
  ],
  "totals": {"total_orders":144, "total_qty":2802, "stages":{...}, "statuses":{...}}
}
```

role 자동 필터:
- hq-admin: 전체
- wh-manager-1: source/target 의 wh = 1 인 row 만
- branch-clerk (store_id=3): target_location_id = 3 만

### 5.2 items 조회 + 검색

```bash
curl -s "http://decision-svc/decision/plan-daily/2026-05-14/items?status=PENDING&q=데미안&limit=50" \
  -H "Authorization: $TOKEN" | jq
```

- `status` / `order_type` 필터
- `q`: `isbn13` OR `books.title` OR `source/target.name` ILIKE
- `offset` + `limit` (max 500)

검증:
```sql
SELECT COUNT(*)
  FROM pending_orders po
  LEFT JOIN books b ON b.isbn13 = po.isbn13
  LEFT JOIN locations sl ON sl.location_id = po.source_location_id
  LEFT JOIN locations tl ON tl.location_id = po.target_location_id
 WHERE forecast_rationale->>'plan_snapshot_date' = '2026-05-14'
   AND status = 'PENDING'
   AND (po.isbn13 ILIKE '%데미안%' OR b.title ILIKE '%데미안%'
        OR sl.name ILIKE '%데미안%' OR tl.name ILIKE '%데미안%');
-- API total 과 동일해야 함
```

### 5.3 /queue 검색 (intervention)

```bash
curl -s "http://intervention-svc/intervention/queue?q=9788956" \
  -H "Authorization: $TOKEN" | jq '.items | length'
```

isbn13 prefix 매칭 → 해당 row 만.

---

## 6. SQL 검증 명령 모음

### 6.1 status 별 count

```sql
SELECT status, COUNT(*)
  FROM pending_orders
 WHERE forecast_rationale->>'plan_snapshot_date' = '2026-05-14'
 GROUP BY status
 ORDER BY status;
```

### 6.2 audit_log inventory delta 추적 (특정 order)

```sql
SELECT created_at, actor_id, entity_id,
       after_state->>'delta' AS delta,
       after_state->>'reason' AS reason
  FROM audit_log
 WHERE action = 'inventory.adjust'
   AND after_state->>'order_id' = '<oid>'
 ORDER BY created_at;
```

### 6.3 WH_TRANSFER 양측 정합 (HAVING COUNT=2)

```sql
SELECT po.order_id, COUNT(*) AS approved_sides
  FROM pending_orders po
  JOIN order_approvals oa USING (order_id)
 WHERE po.order_type = 'WH_TRANSFER'
   AND po.status = 'APPROVED'
   AND oa.decision = 'APPROVED'
   AND oa.approval_side IN ('SOURCE','TARGET')
 GROUP BY po.order_id
HAVING COUNT(*) = 2;
-- APPROVED WH_TRANSFER 는 반드시 2 row (SOURCE+TARGET)
```

### 6.3b REBALANCE 양측 정합 (2026-05-14 양측 협의)

```sql
-- REBALANCE 양측 정합 — APPROVED 인데 SOURCE+TARGET 양쪽 모두 안 찍힌 row 검출
SELECT po.order_id, COUNT(oa.approval_id) AS approvals
  FROM pending_orders po
  JOIN order_approvals oa ON oa.order_id = po.order_id
 WHERE po.order_type='REBALANCE' AND po.status='APPROVED'
   AND oa.decision='APPROVED' AND oa.approval_side IN ('SOURCE','TARGET')
 GROUP BY po.order_id HAVING COUNT(*) != 2;
-- 0 row 면 정합 OK (모든 APPROVED REBALANCE 가 양측 모두 승인됨)
```

### 6.4 inventory before/after 비교 (단일 책)

```sql
SELECT l.name, i.on_hand
  FROM inventory i
  JOIN locations l USING (location_id)
 WHERE i.isbn13 = '<isbn>'
 ORDER BY l.location_type DESC, l.name;
```

### 6.5 REBALANCE source -qty 정합

```sql
SELECT po.order_id, po.qty,
       (a1.after_state->>'delta')::int AS audit_delta
  FROM pending_orders po
  JOIN audit_log a1
    ON a1.action='inventory.adjust'
   AND a1.after_state->>'order_id' = po.order_id::text
 WHERE po.order_type='REBALANCE'
   AND po.status IN ('APPROVED','EXECUTED')
   AND (a1.after_state->>'reason') LIKE '양측 승인 출고:%';
-- audit_delta = -qty 여야 함 (REBALANCE 양측 협의 적용 후 reason='양측 승인 출고')
```

### 6.6 거부 후 source 복원 정합

```sql
SELECT po.order_id, po.qty,
       SUM((a.after_state->>'delta')::int) AS net_delta
  FROM pending_orders po
  JOIN audit_log a
    ON a.action='inventory.adjust'
   AND a.after_state->>'order_id' = po.order_id::text
 WHERE po.status = 'REJECTED'
   AND po.order_type IN ('REBALANCE','WH_TRANSFER')
 GROUP BY po.order_id, po.qty
HAVING SUM((a.after_state->>'delta')::int) != 0;
-- net_delta=0 이어야 함 (-qty + +qty = 0). 결과가 1 row 라도 정합 위반.
```

---

## 7. 알림 시점 매트릭스

intervention-svc / decision-svc 가 notification-svc `/send` 로 발행하는 event 모음. severity 별 channel 자동 결정 (Logic Apps spam 방지 — 2026-05-13 Notion 알람 명세).

| 시점 / 동작 | event_type | severity | channels | 수신자 |
| --- | --- | --- | --- | --- |
| `/approve` 단건 | OrderApproved | INFO | redis, websocket | 본인 + hq-admin digest |
| `/reject` 단건 | OrderRejected | WARNING | redis, websocket | 본인 + 발의 stage 관계자 |
| `/intervene/batch` 또는 `/approve-all-today` | OrderApproved (batch_size N) | INFO | redis, websocket | hq-admin digest |
| `/inbound/<oid>/receive` | OrderExecuted | INFO | redis, websocket | 본인 + 발의 wh-manager |
| `/inbound/<oid>/reject` | InboundRejected | WARNING | websocket, logic-apps | 발의 wh-manager + hq-admin (Teams) |
| `/inbound/<oid>/reject` (CRITICAL = 파손 多) | InboundRejected | CRITICAL | redis, websocket, logic-apps, sms | hq-admin + wh-manager + on-call |
| `/returns/request` | ReturnPending | INFO | redis, websocket | hq-admin digest |
| `/returns/approve` | ReturnPending | INFO | redis, websocket | branch-clerk + wh-manager |
| `/returns/reject` (hq only) | ReturnRejected | WARNING | redis, websocket | branch-clerk |
| `/plan-daily` 발의 | DailyPlanFinalized | INFO | websocket, logic-apps | hq-admin + wh-manager 전체 (Teams) |
| `/plan-daily` AUTO_EXECUTED CRITICAL | AutoExecutedUrgent | CRITICAL | redis, websocket, logic-apps, sms | hq-admin + on-call |
| spike-detect Lambda (Kinesis) | SpikeUrgent | WARNING | websocket, logic-apps | hq-admin + wh-manager |
| `/new-book-requests/<id>/approve` | NewBookRequest | INFO | websocket, logic-apps | 출판사 publisher-watcher CronJob 트리거 대상 |
| 승인 D+3 미처리 | ApprovalDelayed | WARNING | websocket, logic-apps | 해당 stage approver |
| Lambda alarm (cw-alarm trigger) | LambdaAlarm | CRITICAL | redis, websocket, logic-apps, sms | hq-admin + on-call |
| CodeDeploy rollback | DeploymentRollback | CRITICAL | redis, websocket, logic-apps, sms | hq-admin + dev team |

수신자 그룹 정의는 notification-svc 의 `recipient_groups` 테이블 (V6.2 schema v4) 기준.
