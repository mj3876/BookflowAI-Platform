# BOOKFLOW AWS deploy CLI

> Python + boto3 기반. PowerShell 5.1 + AWS CLI v2 의 한국 Windows cp949 인코딩 이슈 회피.

## 설치

```bash
pip install -r scripts/aws/requirements.txt
aws configure   # access key + secret + region=ap-northeast-1
```

## 사용법

```bash
cd "C:\Users\User\Desktop\kyobo project\BookFlowAI-Platform"

# Phase 0 · 영구 자원 (Day 0 1회)
python scripts/aws/bookflow.py phase0
python scripts/aws/bookflow.py phase0 --down   # 학기 종료 시

# 매일 base
python scripts/aws/bookflow.py base-up
python scripts/aws/bookflow.py base-down

# Tasks (개별)
python scripts/aws/bookflow.py task data
python scripts/aws/bookflow.py task msa-pods
python scripts/aws/bookflow.py task etl-streaming
python scripts/aws/bookflow.py task publisher
python scripts/aws/bookflow.py task auth-pod         # env BOOKFLOW_AZURE_VPN_GW_IP
python scripts/aws/bookflow.py task forecast         # env BOOKFLOW_GCP_VPN_GW_IP
python scripts/aws/bookflow.py task lambdas
python scripts/aws/bookflow.py task glue
python scripts/aws/bookflow.py task client-vpn
python scripts/aws/bookflow.py task rds-seed

# 통합 (data + msa-pods + etl + publisher)
python scripts/aws/bookflow.py task --all
python scripts/aws/bookflow.py task --all --down

# 개별 destroy
python scripts/aws/bookflow.py task <name> --down

# HA 시나리오
python scripts/aws/bookflow.py scenario ha
python scripts/aws/bookflow.py scenario ha --revert

# 모든 자원 + Tier 00 영구까지 (학기 종료)
python scripts/aws/bookflow.py wipe-all

# 상태 확인
python scripts/aws/bookflow.py status
```

## 폴더 구조

```
scripts/aws/
├── bookflow.py              # 메인 CLI entry point
├── requirements.txt
├── README.md
├── lib/
│   ├── config.py            # account/region/prefix · INFRA_ROOT
│   ├── log.py               # rich 색상 출력
│   └── stack.py             # boto3 wrapper · ChangeSet 패턴 deploy
└── tasks/
    ├── foundation.py        # phase0 (Tier 00 영구)
    ├── base.py              # base-up · base-down
    ├── data.py              # Tier 20 RDS + Redis + Kinesis
    ├── msa_pods.py          # EKS · IRSA · endpoints · peering
    ├── etl_streaming.py     # ECS sims
    ├── publisher.py         # ALB + WAF + Publisher ASG + inventory-api
    ├── auth_pod.py          # NAT + Azure VPN
    ├── forecast.py          # GCP VPN
    ├── lambdas_.py          # SAM · 7 Lambdas
    ├── glue.py              # Glue Catalog + Step Functions ETL3
    ├── client_vpn.py        # Client VPN Endpoint
    ├── rds_seed.py          # Ansible peering (placeholder)
    ├── full_stack.py        # 통합
    ├── scenario_ha.py       # HA toggle
    └── wipe_all.py          # 학기 종료 시
```

## 환경 변수

| 이름 | 용도 | 기본값 |
|------|------|--------|
| `AWS_REGION` | 리전 | ap-northeast-1 |
| `BOOKFLOW_PROJECT` | 프로젝트 prefix | bookflow |
| `BOOKFLOW_AZURE_VPN_GW_IP` | task-auth-pod 활성 IP | — |
| `BOOKFLOW_AZURE_VPN_PSK` | (선택) | AWS 자동 |
| `BOOKFLOW_GCP_VPN_GW_IP` | task-forecast 활성 IP | — |
| `BOOKFLOW_GCP_VPN_PSK` | (선택) | GCP terraform |

## Tier 의존성

```
00 (영구) ─┬─→ 10 (network) ─┬─→ 20 (data) ─→ 40 (runtime) ─→ 50 (NAT/ALB) ─→ 60 (cross-cloud)
           │                  └─→ 30 (cluster) ─→ 40
           ├─→ 99-glue
           └─→ 99-serverless ─→ 10/20
```

## Day-to-day 운영

```bash
# 09:00 아침
python scripts/aws/bookflow.py base-up
python scripts/aws/bookflow.py task --all      # 통합 시연 시

# 18:00 저녁
python scripts/aws/bookflow.py base-down       # 전체 destroy (Tier 00 제외)
```

## 비용

| 단계 | 추가 일 비용 |
|---|---|
| Tier 00 영구 | ~$1/일 |
| base-up | +$1/일 |
| task-data | +$1.30/일 |
| task-msa-pods | +$1.20/일 |
| task-etl-streaming | +$0.40/일 |
| task-publisher | +$1.70/일 |
| task-auth-pod | +$1.50/일 |
| task-forecast | +$1.20/일 |
| task-client-vpn | +$1.50/일 |
| task-lambdas / task-glue | ~$0 (free tier) |

총 통합 시 ~$10/일 · 25 영업일 운영 시 ~$200/월 (비용산정 V1 일치)
