# Tier 50 · Network Traffic (📆 Phase 기반 · task-publisher · task-auth-pod)

## 이 Tier의 역할

**External ALB · WAF · NAT Gateway** — 외부 트래픽 진입 + 인증 outbound. base-up 미포함 · 작업 시 task 가 deploy.

## Stack (3개)

| YAML | 내용 | 사용 task |
|---|---|---|
| `nat-gateway.yaml` | NAT Gateway × 2 (Egress Public · Multi-AZ HA) + EIP × 2 | task-auth-pod (TGW 활성 시 cross-VPC) |
| `alb-external.yaml` | External ALB (Egress Public) + 3 Target Groups (Publisher Blue · Green · Inventory API) + Listener 80 (PROD) + 8080 (TEST) | task-publisher |
| `waf.yaml` | WAFv2 WebACL + ALB Association + AWS Managed Rules + Rate Limit + CW Logs | task-publisher |

## ALB 구조

```
Internet
    ↓
[WAF · Rate Limit + Managed Rules]
    ↓
[External ALB · Egress Public · 2 AZ]
    ├─ Listener 80 (PROD)
    │   ├─ Default action  → publisher-blue-tg (instance · ASG)
    │   └─ Rule /api/inventory/* (priority 10) → inventory-api-tg (ip · ECS Fargate)
    └─ Listener 8080 (TEST)
        └─ Default action  → publisher-green-tg (CodeDeploy 검증 traffic)
```

## CodeDeploy Blue/Green 동작 (V6.2 Slide 22)

1. **초기 상태**: Blue TG ← Publisher ASG · 현재 production traffic
2. **CodeDeploy 배포 시작**: 새 ASG 생성 + Green TG 에 등록 + Listener 8080 으로 검증
3. **검증 완료 후**: Listener 80 의 default action 이 Blue → Green 으로 swap
4. **이전 Blue ASG 종료** (또는 keep for rollback)

→ Blue · Green TG 둘 다 미리 준비 (CFN) · CodeDeploy 가 ASG 와 연결만 swap.

## Inventory API 동작 (V6.2 Slide 6)

```
Sales Data ECS sim → Internet → External ALB :80 /api/inventory/*
                                          ↓
                                    inventory-api-tg
                                          ↓
                                    ECS Fargate inventory-api
                                          ↓ (Peering egress-data)
                                          RDS read
```

## NAT Gateway 사용 시점

| Phase | NAT 사용 | 흐름 |
|---|---|---|
| Phase 1-2 (build/dev) | ❌ 안 함 | Pod → 직접 호출 안 시도 · 로컬 PC 가 public 으로 테스트 |
| Phase 3-4 (시나리오/시연) | ✅ 활성 | auth-pod (BookFlow AI Private) → TGW (Tier 60) → 이 NAT → IGW → Azure Entra OIDC |

**중요**: NAT 는 만들어두지만 Peering 으론 cross-VPC NAT 안 됨. Tier 60 TGW 활성 시에만 실제 사용.

## Import 매트릭스

### nat-gateway.yaml
- `bookflow-subnet-egress-public-az1/az2`

### alb-external.yaml
- `bookflow-vpc-egress-id`
- `bookflow-subnet-egress-public-az1/az2`

### waf.yaml
- `bookflow-alb-external-arn` (alb-external.yaml 후)

## 배포 순서 (task-publisher.ps1)

```
1. alb-external      ← VPC + Subnet Import · ALB 생성 (~3분)
2. waf               ← alb-external Import · WebACL + Association
3. publisher-asg     ← TargetGroupArn = publisher-blue-tg-arn 주입 (update-stack)
4. ecs-inventory-api ← TargetGroupArn = inventory-api-tg-arn 주입 (update-stack)
```

task-auth-pod.ps1 은:
```
1. endpoints-bookflow-ai (skip if exists)
2. nat-gateway (Tier 50)
3. (Tier 60 vpn-site-to-site Azure - 추후 작성)
```

## 검증

```powershell
# lint
cfn-lint infra\aws\50-network-traffic\*.yaml

# ALB DNS 확인
aws cloudformation describe-stacks --stack-name bookflow-50-alb-external --query 'Stacks[0].Outputs[?OutputKey==`AlbDnsName`].OutputValue' --output text

# WAF 부착 확인
aws wafv2 get-web-acl-for-resource --resource-arn <ALB_ARN>

# NAT EIP 확인
aws cloudformation describe-stacks --stack-name bookflow-50-nat-gateway --query 'Stacks[0].Outputs'

# ALB → Publisher 통신 (Publisher 가 nginx 응답 시)
curl http://<ALB_DNS>/

# inventory-api 동작
curl http://<ALB_DNS>/api/inventory/health
```

## CI/CD 연결 포인트

| 자원 | CI/CD update | 어떻게 |
|---|---|---|
| Publisher ASG | TargetGroupArn 주입 후 CodeDeploy 가 Blue/Green swap | task-publisher 가 자동 주입 |
| inventory-api | TargetGroupArn 주입 후 ECS rolling | task-publisher 가 자동 주입 |
| ALB Listener Rules | 추후 추가 path (예: /api/auth/*) | update-stack 또는 별도 yaml |

## 비용 (Tier 50 · Phase 기반 · 198h × 22d)

| 자원 | 시간당 | 월 비용 (full 가동) |
|---|---|---|
| External ALB | $0.0225 + LCU | $4.46 + ~$5 LCU |
| WAF (Web ACL) | $5 fixed + Rules | ~$5/월 + ~$1 (rules) |
| WAF Requests | $0.60/M | minimal |
| NAT Gateway × 2 | $0.062 × 2 | $24.55 + 데이터 |
| EIP × 2 (NAT 부착 · 사용 중) | $0 | $0 |

**합계 예상**: ~$40/월 if running 198h. 비용산정 V1 은 NAT 만 108h 가정 = $13.58.

→ **task-publisher / task-auth-pod 가 필요할 때만 deploy** → 실제 사용 시간만 과금.

## 비고

- ALB Listener HTTPS (443) 미구성 → ACM 발급 후 Listener 추가 (별도 PR)
- WAF Managed Rules 3종: CommonRuleSet · KnownBadInputs · IpReputationList (OWASP 부분 + IP 평판)
- Rate Limit: IP 당 5분당 2000 req (DDoS 완화 · 데모 환경 적당)
- Internal ALB 는 K8s ALB Controller 가 Ingress yaml 보고 자동 생성 (CFN 무관)
