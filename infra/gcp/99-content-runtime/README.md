# GCP Content Runtime — AI 수요예측 파이프라인

> BOOKFLOW · GCP AI/ML 레이어  
> 도서 판매 수요 예측 → 지점 재분배 · 발주 권고

---

## 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────────┐
│                        데이터 수집 레이어                         │
│  AWS S3 Mart ──► GCS Staging ──► bq-load CF ──► BigQuery DW     │
│  (POS / 재고 / 외부이벤트 / 도서메타)                              │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
┌─────────────▼──────────────┐  ┌──────────▼──────────────────────┐
│     [주 1회] 모델 재학습     │  │    [일 1회] 배치 예측            │
│                             │  │                                  │
│  Vertex AI Pipeline (KFP)   │  │  Vertex AI BatchPredictionJob    │
│  ┌─────────────────────┐    │  │  ┌──────────────────────────┐   │
│  │ 1. enriched 뷰 갱신  │    │  │  │ v_automl_forecast_input  │   │
│  │    sales × books ×  │    │  │  │ → 전체 isbn × 지점 조합  │   │
│  │    locations × 이벤트│    │  │  │ → 30일 일별 판매량 예측  │   │
│  ├─────────────────────┤    │  │  └──────────────┬───────────┘   │
│  │ 2. AutoML Forecasting│    │  │                 │               │
│  │    학습 (~1시간)     │    │  │  ┌──────────────▼───────────┐   │
│  ├─────────────────────┤    │  │  │  forecast_results (BQ)   │   │
│  │ 3. Model Registry   │    │  │  └──────────────┬───────────┘   │
│  │    등록              │    │  │                 │               │
│  └─────────────────────┘    │  │  ┌──────────────▼───────────┐   │
│                             │  │  │  비즈니스 로직 뷰 (BQ)    │   │
│  BQML Pipeline (별도)       │  │  │  ├ v_redistribution_reco  │   │
│  → bookflow_existing_books_ │  │  │  │   재고 부족 지점 알림    │   │
│    forecast (BQML 모델)     │  │  │  └ v_procurement_reco     │   │
│  → bookflow_new_books_      │  │  │      WH별 발주 권고량     │   │
│    forecast (BQML 모델)     │  │  └──────────────────────────┘   │
└─────────────────────────────┘  └──────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                     [실시간] 신간 수요 예측                        │
│                                                                  │
│  신간 ISBN 등록 ──► new-book-inference CF ──► BQML ML.PREDICT    │
│                        └─► 지점별 초기 배분 수량 즉시 반환        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 모델 구성

### 1. AutoML Forecasting (기존 도서 배치 예측)

| 항목 | 내용 |
|------|------|
| 타입 | Vertex AI AutoML Forecasting (Time Series) |
| 학습 주기 | **주 1회** (매주 월요일 새벽, Cloud Scheduler) |
| 예측 기간 | **30일** (재분배용 3일 + 발주용 30일 통합) |
| 예측 단위 | isbn13 × store_id (일별 판매량 qty_sold) |
| 학습 시간 | ~1시간 / 비용 ~$20/회 |
| 컨텍스트 | 과거 60일 기준 |

**학습 피처:**

```
[정적 속성 — 시계열별 고정값]
  store_id, wh_id, store_size (S/M/L), region (수도권/영남)
  category_id, price_tier, is_bestseller_flag, author_experience_years

[미래에 알 수 있는 값 — available_at_forecast]
  is_holiday, event_nearby_days, season, day_of_week, month, is_weekend

[과거에만 알 수 있는 값 — unavailable_at_forecast]
  qty_sold (타겟), revenue, avg_price, sns_mentions_1d, sns_mentions_7d
```

### 2. BQML BOOSTED_TREE_REGRESSOR (신간 도서 실시간 예측)

| 항목 | 내용 |
|------|------|
| 타입 | BigQuery ML BOOSTED_TREE_REGRESSOR |
| 학습 주기 | **주 1회** (Vertex AI Pipeline, 비용 거의 $0) |
| 예측 방식 | 실시간 ML.PREDICT (HTTP 요청당 즉시 응답) |
| 예측 단위 | isbn13 기준 → 전 지점 배분량 반환 |
| 학습 피처 | category_id, price_tier, sales_point, is_bestseller_flag, author_experience_years, author_past_books_count, item_page, store_id, **region_code**, **size_numeric** |

---

## 출력 — 비즈니스 액션

### 재분배 추천 (`v_redistribution_reco`)
```
isbn13      store_id  on_hand  predicted_3d_demand  surplus_deficit
9791234...  3 (잠실)  50       120                  -70  ← 부족, 이동 필요
9791234...  1 (강남)  300      80                   +220 ← 잉여, 공급 가능
```

### 발주 권고 (`v_procurement_reco`)
```
isbn13      wh_id  predicted_30d_demand  current_wh_stock  order_qty
9791234...  1      1,200                 400               800
```

---

## Cloud Functions

| 함수명 | 트리거 | 역할 |
|--------|--------|------|
| `bookflow-bq-load` | GCS Finalize 이벤트 | GCS staging → BQ 테이블 적재 |
| `bookflow-feature-assemble` | HTTP | 신간 추론 피처 조립 |
| `bookflow-vertex-invoke` | HTTP | Vertex AI private endpoint 호출 |
| `bookflow-new-book-inference` | HTTP | BQML ML.PREDICT 실행, 결과 BQ 저장 |

---

## 주요 BQ 테이블 / 뷰

| 이름 | 유형 | 설명 |
|------|------|------|
| `sales_fact` | 테이블 | 일별 isbn × 지점 판매 실적 |
| `books_static` | 테이블 | 도서 메타 (category, price_tier 등) |
| `locations_static` | 테이블 | 지점 정보 (16개: 오프라인 12 + 온라인 2 + WH 2) |
| `store_location_map` | 테이블 | 판매 지점 → 재고 위치 매핑 (14개) |
| `features` | 테이블 | 일별 외부 피처 (공휴일, SNS, 이벤트 등) |
| `v_automl_forecast_input` | 뷰 | AutoML 학습용 enriched 조인 뷰 |
| `new_book_training_dataset` | 테이블 | BQML 신간 모델 학습용 데이터 |
| `new_book_forecast` | 테이블 | 신간 지점별 예측 결과 |
| `forecast_results` | 테이블 | AutoML 배치 예측 결과 (30일) |
| `bookflow_existing_books_forecast` | BQML 모델 | 기존 도서 수요 예측 모델 |
| `bookflow_new_books_forecast` | BQML 모델 | 신간 초기 수요 예측 모델 |

---

## 지점 구성 (RDS 기준 정합)

| wh_id | region | 오프라인 지점 | 온라인 | WH |
|-------|--------|--------------|--------|-----|
| 1 | 수도권 | 강남(L) 광화문(L) 잠실(M) 홍대(M) 신촌(S) 용산(S) | 수도권온라인 | 수도권거점창고 |
| 2 | 영남 | 부산서면(L) 대구동성(L) 울산삼산(M) 대구교대(M) 부산센텀(S) 포항양덕(S) | 영남온라인 | 영남거점창고 |

---

## 배포

```powershell
# 99-content-runtime 단독 배포
cd infra\gcp\99-content-runtime
terraform init
terraform apply

# 전체 GCP 스택 배포
.\scripts\gcp\deploy-all.ps1

# 일별 정리 (20-network-daily + 99-content-runtime)
.\scripts\gcp\destroy-gcp-layers.ps1
```

> **주의**: `terraform destroy` 시 BQ dataset 및 BQML 모델은 Terraform 외부에서 관리되므로 삭제되지 않습니다. Vertex AI Endpoint와 Cloud Functions만 삭제됩니다.

---

## 백업

BQML 모델 및 Vertex AI 모델은 `backup-494808` 프로젝트에 복사 보관합니다.

```
backup-494808
├── bookflow_dw (BQ dataset)
│   ├── bookflow_existing_books_forecast  (BQML 모델)
│   ├── bookflow_new_books_forecast       (BQML 모델)
│   ├── books_static, locations_static, store_location_map
│   ├── new_book_training_dataset, new_book_forecast
│   └── [GCS export] gs://backup-494808-bookflow-models/
│       ├── vertex-models/bookflow-xgb-demand-v1/model.bst
│       └── backup/ (BQML export artifacts)
└── Model Registry
    └── bookflow-xgb-demand-forecast  (XGBoost online 서빙 모델)
    └── bookflow-sales-forecast-v2    (AutoML Forecasting, 학습 완료 후)
```

백업 스크립트: `scripts/export_models.py`, `scripts/vertex_deploy_and_backup.py`
