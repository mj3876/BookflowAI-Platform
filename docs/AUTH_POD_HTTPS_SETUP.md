# Auth-pod HTTPS Setup (DuckDNS + Let's Encrypt)

Phase γ (auth-pod 실 구현) 시 HTTPS / 도메인 / cert 셋업 + Phase 4 (private EKS) 전환 가이드.

## 자원 매핑

| 자원 | 위치 | 비고 |
|---|---|---|
| **DuckDNS 도메인** | `bookflow.duckdns.org` | YHK0427@github · 무료 · 5/계정 |
| **DuckDNS Token** | `scripts/aws/config/.env.local` (`BOOKFLOW_DUCKDNS_TOKEN`) | gitignored |
| **Cert** | Let's Encrypt R13 (90일 자동 갱신 · cert-manager) | DNS-01 challenge via DuckDNS webhook |
| **TLS 종료점** | NGINX Ingress Controller (NLB) | dashboard-svc 등 모든 service 의 진입점 |
| **EKS 진입** | NGINX Ingress NLB → bookflow.duckdns.org A record | 사용자 → HTTPS → NLB → Pod |
| **Pod outbound (Entra)** | TGW → Egress NAT GW → Internet → login.microsoftonline.com | 인증 token 교환 |

## Idempotent 자동화 — `task eks-addons`

매일 destroy/redeploy 시 helm + manifests 모두 자동 재현:

```bash
py scripts/aws/bookflow.py task eks-addons        # deploy
py scripts/aws/bookflow.py task eks-addons --down # destroy
```

수행 내용:
1. helm upgrade --install **ingress-nginx** (NLB · internet-facing)
2. helm upgrade --install **cert-manager** (with `--dns01-recursive-nameservers=8.8.8.8:53,1.1.1.1:53` flag — EKS Worker Node 의 외부 :53 차단 우회)
3. helm upgrade --install **cert-manager-webhook-duckdns** (mmontes11 chart · DuckDNS DNS-01 solver)
4. K8s Secret `duckdns-token` (in `ingress-nginx` ns) — `.env.local` 의 `BOOKFLOW_DUCKDNS_TOKEN` 으로 생성
5. ClusterIssuer (`letsencrypt-prod` · `letsencrypt-staging`) — DNS-01 + `apiTokenSecretRef`
6. Certificate (`bookflow-tls` · 90일 cert · auto renew 15일 전)
7. duckdns-sync CronJob (5분마다 NLB hostname → DuckDNS A record sync · ServiceAccount + Role)
8. dashboard-svc Ingress (HTTPS · TLS via cert-manager · ssl-redirect)

소스: `scripts/aws/tasks/eks_addons.py` + Apps repo `eks-pods/{auth-pod,dashboard-svc,duckdns-sync}/k8s/`

## 수동 셋업 (1회 · 추가 작업)

`task eks-addons` 가 hands-off 자동이지만 외부 시스템 (DuckDNS · Entra) 한 번만 사람 손 필요:

1. **DuckDNS 계정 + 도메인 신청** (1회): https://www.duckdns.org/ → GitHub 로그인 → `bookflow` 서브도메인 추가 → token 복사 → `.env.local` 의 `BOOKFLOW_DUCKDNS_TOKEN=...` 에 저장
2. **Entra App registration redirect URI** (Phase γ 시작 시): Azure Portal → Entra ID → App registrations → BookFlow auth-pod → Authentication → Web redirect URI: `https://bookflow.duckdns.org/auth/callback` 추가

## 핵심 트러블슈팅

### cert 발급 시 "DNS-01 challenge propagation: dial tcp X.X.X.X:53: i/o timeout" (해결)
- 원인: cert-manager 의 propagation self-check 가 authoritative DNS (DuckDNS NS) 에 직접 dial. EKS Worker Node 의 outbound :53 이 외부 IP 로 차단됨 (SG/NACL).
- 해결: cert-manager Helm 의 `extraArgs` 에 `--dns01-recursive-nameservers-only` + `--dns01-recursive-nameservers=8.8.8.8:53,1.1.1.1:53` 추가. → public recursive resolver 사용 → 54초 만에 cert 발급. **이미 `eks_addons.py` 에 적용됨**.
- 노션: 트러블슈팅 DB · `auth-pod HTTPS · Let's Encrypt cert-manager DNS-01 propagation check timeout` (2026-05-06)

### "Too many pods" Pod scheduling error
- EKS VPC CNI 의 ENI-based IP allocation 한계 (1 노드당 ~17 pod · m5.large).
- 해결: nodegroup desiredCapacity 증가 (1 → 2+) 또는 VPC CNI prefix assignment.

### cert-manager-webhook-duckdns chart URL
- ebrianne 원본 (`https://ebrianne.github.io/helm-charts`) → **404** (repo 다운).
- 대체: **mmontes11 fork** (`https://mmontes11.github.io/charts`) · v1.2.3 · groupName=`acme.duckdns.org` · solverName=`duckdns` · config schema=`apiTokenSecretRef.{name,key}`.

### Let's Encrypt rate limit
- production: 5 cert / 7day per domain.
- staging (`letsencrypt-staging`): 무제한 (untrusted cert · 개발 검증용).

## Phase 4 전환 (private EKS + Client VPN) 시 작업

**작동 유지** (변경 없음):
- DuckDNS DNS resolution (public)
- Let's Encrypt cert 자동 갱신 (DNS-01 + DuckDNS API outbound 만 필요)
- OIDC redirect URI (`https://bookflow.duckdns.org/auth/callback`)
- auth-pod → Entra outbound (TGW + NAT)

**변경 필요**:

### 1. NGINX Ingress LB scheme 변경 (internet-facing → internal)
helm value 추가 후 redeploy:
```python
# eks_addons.py 에 환경 분기 추가 예정
"--set", "controller.service.annotations.service\\.beta\\.kubernetes\\.io/aws-load-balancer-scheme=internal",
```

### 2. EKS endpoint private 전환
```bash
aws cloudformation update-stack \
  --stack-name bookflow-30-eks-cluster \
  --use-previous-template \
  --parameters ParameterKey=EKSEndpointPublic,ParameterValue=false ...
```

### 3. Client VPN deploy
```bash
py scripts/aws/bookflow.py task client-vpn
```
- Phase 4+ 만 deploy ($21/월). 운영자 (영헌·민지·우혁) 3명 cert 생성 + ovpn 파일 배포.

### 4. duckdns-sync CronJob 자동 처리
- 이미 5분마다 NLB hostname 체크 → DuckDNS A record sync. Phase 4 전환 시 internal NLB 의 새 hostname 자동 반영.

## DNS-01 challenge 흐름 (참고)

1. Certificate 생성 → cert-manager 가 ACME Order 시작
2. cert-manager 가 challenge 요청 → DuckDNS webhook (helm chart) 호출
3. webhook 이 DuckDNS API (`https://www.duckdns.org/update?...&txt=<token>`) 호출 → TXT record 등록
4. cert-manager 가 propagation check (8.8.8.8 / 1.1.1.1 recursive resolver 사용)
5. 통과 → ACME 서버에 ready 신호 → Let's Encrypt 가 TXT 검증 → cert 발급
6. K8s Secret `bookflow-tls` (in `bookflow` ns) 에 `tls.crt` + `tls.key` 저장
7. dashboard-svc Ingress 가 Secret 참조 → HTTPS 종료
