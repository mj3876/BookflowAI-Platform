# 시나리오 테스트 명세서

> 대상 스크립트: `eks-pod-failure-test.sh` / `eks-autoscaling-test.sh` / `logic-apps-rate-limit-test.sh`
> 작성 기준일: 2026-05-20

---

## Scenario A — EKS Pod 장애 (`eks-pod-failure-test.sh`)

### 검증 목표

Running 상태의 Pod를 강제 삭제했을 때 Deployment Controller가 자동으로 신규 Pod를 재생성하며, 그 사이 ALB Target Health 이상 없이 서비스 무중단(5xx 미발생)이 유지되는지 검증한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| 클러스터 | `bookflow-eks` 정상 동작 중 |
| 네임스페이스 | `bookflow` 내 Running Pod 1개 이상 |
| kubectl | `bookflow` 네임스페이스 접근 권한 |
| AWS CLI | `ap-northeast-1` 인증 완료 |
| ALB | `bookflow-alb-external` 및 Target Group 2개 등록 완료 |
| NGINX Ingress | `ingress-nginx` 네임스페이스 Running 상태 |
| Deployment | Replica ≥ 1, 재시작 정책 `Always` |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| 신규 Pod 기동 | 삭제 후 60초 이내 `1/1 Running` |
| ALB Target Health | 삭제 구간 포함 전 타깃 `healthy` 유지 |
| CloudWatch 5xx | 테스트 구간 `HTTPCode_Target_5XX_Count = 0` |
| NGINX Ingress 5xx | 로그에서 `5[0-9]{2}` 응답 0건 |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| A-1 | `notification-svc` Pod를 자동 선택하여 강제 삭제 실행 | `kubectl delete pod` 정상 종료 메시지 확인 |
| A-2 | 삭제 후 Deployment Controller가 신규 Pod를 재생성하는지 확인 | `kubectl get pods -n bookflow` 에서 신규 Pod 이름으로 `1/1 Running` 전환 확인 |
| A-3 | 재생성 중 ALB Target Group에서 삭제된 Pod IP가 `draining → deregistered` 되는지 확인 | `aws elbv2 describe-target-health` 결과에 `unhealthy` 미포함 |
| A-4 | CloudWatch ALB 메트릭에서 5xx 발생 여부 확인 | `HTTPCode_Target_5XX_Count` Sum = 0 |
| A-5 | NGINX Ingress Controller 로그에서 5xx 응답 여부 확인 | `kubectl logs` 필터링 결과 0건 |
| A-6 | `all` 모드로 전체 시나리오 자동 실행 및 최종 요약 출력 | 요약 테이블 모든 항목 ✅ |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| A-1 | 터미널 전체 — `bash eks-pod-failure-test.sh all` 실행 화면 | 터미널 전체 화면 녹화 (시작~완료) |
| A-2 | `[+Xs] Running` 로그 출력 구간 — Pod 상태 전이 (Terminating → ContainerCreating → Running) | 녹화 중 해당 구간 타임스탬프 메모 |
| A-3 | ALB Target Health 테이블 출력 — drain 직후 + 복구 후 2회 비교 | 터미널 스크롤 캡처 또는 전체 녹화 |
| A-4 | 검증 결과 요약 테이블 (`section 검증 결과`) 출력 화면 | 터미널 정지 화면 스크린샷 |
| A-5 | AWS Console → CloudWatch → ALB 5xx 그래프 (테스트 시간 구간) | 브라우저 화면 스크린샷 |

---

## Scenario B — EKS Node 오토스케일링 (`eks-autoscaling-test.sh`)

### 검증 목표

Node를 강제 drain하여 해당 Node의 Pod이 Pending으로 전환될 때, Cluster Autoscaler가 ASG scale-up을 트리거하여 신규 Node를 프로비저닝하고 Pod가 재배치되는 전 과정을 검증한다. 테스트 완료 후 drain된 Node를 uncordon하여 원복한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| 클러스터 | `bookflow-eks` Ready Node ≥ 2개 |
| Cluster Autoscaler | `kube-system/cluster-autoscaler` Pod `1/1 Running` |
| ASG 태그 | `k8s.io/cluster-autoscaler/enabled=true`, `k8s.io/cluster-autoscaler/bookflow-eks=owned` |
| ASG 설정 | Min: 2 / Max: 3 / Desired: 2 |
| IRSA | `bookflow-cluster-autoscaler` IAM Role OIDC 연결 완료 |
| kubectl | Node drain 권한 포함 |
| AWS CLI | `ap-northeast-1` 인증 완료 |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| ASG Desired 증가 | drain 후 180초 이내 Desired `2 → 3` |
| 신규 Node Ready | 프로비저닝 시작 후 120초 이내 `STATUS=Ready` |
| Pod 재배치 완료 | 신규 Node Ready 후 30초 이내 `bookflow` Pod 전체 `1/1 Running` |
| CA 로그 | `lastScaleUpTime` 타임스탬프 기록 |
| drain Node 복구 | uncordon 후 `Ready` 상태 복귀 |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| B-1 | `bookflow` Pod가 가장 많이 배치된 Node를 자동 선택하여 drain 실행 | `kubectl drain` 완료 후 대상 Node `SchedulingDisabled` 확인 |
| B-2 | drain된 Node의 Pod이 Pending 상태로 전환되는지 확인 | `kubectl get pods -n bookflow` 에서 `Pending` 상태 Pod 수 확인 |
| B-3 | CA가 Pending Pod를 감지하여 ASG scale-up을 트리거하는지 확인 | CA 로그에서 `lastScaleUpTime` 변경 및 `aws autoscaling describe` Desired 증가 |
| B-4 | 신규 Node가 프로비저닝되어 `STATUS=Ready`로 전환되는지 확인 | `kubectl get nodes` 에서 신규 Node 이름 + `Ready` 상태 확인 |
| B-5 | 신규 Node에 Pending Pod가 재배치되어 모두 `Running` 되는지 확인 | `kubectl get pods -n bookflow -o wide` 에서 신규 Node에 `1/1 Running` 확인 |
| B-6 | CA status configmap에 scale-up 이벤트가 기록되는지 확인 | `kubectl describe configmap cluster-autoscaler-status -n kube-system` 확인 |
| B-7 | drain된 Node가 uncordon 후 `Ready` 상태로 복구되는지 확인 | `kubectl get nodes` 에서 `SchedulingDisabled` 해제 + `Ready` 확인 |
| B-8 | `all` 모드로 전체 시나리오 자동 실행 및 최종 요약 출력 | 요약 테이블 모든 항목 ✅ |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| B-1 | 터미널 전체 — `bash eks-autoscaling-test.sh all` 실행 화면 | 터미널 전체 화면 녹화 (시작~완료) |
| B-2 | Node 상태 모니터링 루프 출력 — `NotReady → Ready` 전이 구간 | 녹화 중 해당 구간 타임스탬프 메모 |
| B-3 | ASG Desired 변경 감지 로그 출력 (`[OK] ASG Desired 변경: 2 → 3`) | 터미널 스크롤 캡처 |
| B-4 | 신규 Node Ready 확인 출력 + Pod 재배치 완료 출력 | 터미널 정지 화면 스크린샷 |
| B-5 | CA 로그 출력 (`lastScaleUpTime` 포함 구간) | 터미널 스크롤 캡처 또는 전체 녹화 |
| B-6 | 검증 결과 요약 테이블 (`section 검증 결과`) 출력 화면 | 터미널 정지 화면 스크린샷 |
| B-7 | AWS Console → EC2 → Auto Scaling Groups → 인스턴스 수 변화 (2→3) | 브라우저 화면 스크린샷 |

---

## Scenario C — Logic Apps ACS Rate Limit 장애 (`logic-apps-rate-limit-test.sh`)

### 검증 목표

2026-05-19 실제 발생한 14건 동시 Logic Apps 호출 → ACS 429 장애를 스크립트로 재현하고, `asyncio.Semaphore(1)` 적용으로 동시 호출을 1건으로 직렬화하여 ACS 429가 미발생함을 검증한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| 클러스터 | `notification-svc` Pod `1/1 Running` |
| Logic Apps URL | `NOTIFICATION_LOGIC_APPS_STOCK_DEPART_URL` Secret 설정 완료 |
| Redis | `notification-svc` Redis dedup 연결 정상 |
| RDS | `notifications_log` 테이블 접근 가능 |
| Python 패키지 | Pod 내 `httpx`, `asyncio` 사용 가능 |
| ACS 쿼터 | 재현 단계 DRY_RUN 여부 결정 (실제 호출 시 일일 쿼터 소비 주의) |
| kubectl | `bookflow` 네임스페이스 exec 권한 |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| [재현] 15건 동시 호출 | 429 응답 1건 이상 발생 (또는 DRY_RUN 모의 확인) |
| [수정] Semaphore 값 | `_logic_apps_sem._value == 1` |
| [수정] 타임아웃 설정 | `logic_apps_timeout_seconds ≥ 120.0` |
| [검증] 5건 직렬 호출 | 429 응답 0건, 성공 5/5 |
| [검증] 소요시간 패턴 | 총 소요 ≈ N × 단건 시간 (직렬화 증명) |
| [검증] notifications_log | 테스트 구간 `status='FAILED'` 0건 |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| C-1 | `check` 모드에서 현재 `_logic_apps_sem` 값이 `Semaphore(1)`인지 확인 | 터미널 출력에서 `limit=1` 확인 |
| C-2 | `check` 모드에서 `logic_apps_timeout_seconds ≥ 120.0`인지 확인 | ConfigMap 조회 결과 확인 |
| C-3 | `reproduce 15` — 15건 동시 Logic Apps 직접 호출로 ACS 429 재현 | 터미널 출력 `❌ HTTP 429` 1건 이상 확인 |
| C-4 | `reproduce` 결과 — 동시 호출 총 소요시간이 단건 소요시간과 유사한지 확인 (병렬 실행 증명) | 총 소요시간 < 단건 × N |
| C-5 | `fix` — Pod 내 코드에서 `async with _logic_apps_sem` 사용 여부 확인 | `_post_logic_apps` 소스 inspect 결과 확인 |
| C-6 | `fix` — `kubectl rollout restart` 후 Pod가 정상 재기동되는지 확인 | `kubectl rollout status` 완료 확인 |
| C-7 | `verify 5` — Semaphore(1) 래핑으로 5건 직렬 호출 시 429 미발생 확인 | 터미널 출력 `✅ HTTP 200` 5/5 확인 |
| C-8 | `verify` 결과 — 각 Call의 `대기 시간` 칼럼이 누적 증가하는지 확인 (직렬화 증명) | Call 2 대기 > Call 1 대기, Call 3 > Call 2 순서 확인 |
| C-9 | `verify` 결과 — `notifications_log` 에서 테스트 구간 `FAILED` 0건 확인 | DB 조회 출력에서 FAILED 행 없음 |
| C-10 | `all` 모드로 전체 시나리오 자동 실행 및 최종 요약 출력 | 요약 테이블 `[재현] / [수정] / [검증]` 항목 모두 기대값 일치 |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| C-1 | 터미널 전체 — `bash logic-apps-rate-limit-test.sh all` 실행 화면 | 터미널 전체 화면 녹화 (시작~완료) |
| C-2 | `[재현 단계]` 출력 — 15건 동시 호출 결과 (`❌ HTTP 429` 다수 포함) | 녹화 중 해당 구간 타임스탬프 메모 |
| C-3 | `[재현 단계]` 하단 요약 — `실패(4xx/5xx): N/15` 출력 | 터미널 정지 화면 스크린샷 |
| C-4 | `[수정 단계]` 출력 — `_logic_apps_sem = asyncio.Semaphore(1)` + rollout 완료 | 터미널 정지 화면 스크린샷 |
| C-5 | `[검증 단계]` 출력 — 5건 각 Call 결과 (`✅ HTTP 200`, 대기시간 누적) | 터미널 스크롤 캡처 |
| C-6 | `[검증 단계]` 하단 요약 — `성공(2xx): 5/5` + `[검증 성공]` 출력 | 터미널 정지 화면 스크린샷 |
| C-7 | `notifications_log` 조회 결과 — FAILED 0건, SENT/DEDUP만 표시 | 터미널 정지 화면 스크린샷 |
| C-8 | Azure Portal → Logic Apps → `la-bookflowmj-stock-depart` → 실행 기록 (직렬 실행 시간 간격 확인) | 브라우저 화면 스크린샷 |
| C-9 | 재현 단계 vs 검증 단계 터미널 화면 나란히 비교 (429 발생 / 0건 대비) | 녹화 편집 또는 화면 분할 스크린샷 |

---

## Scenario D — GCP VPN BGP 장애 (`gcp-vpn-failure-test.sh`)

### 검증 목표

GCP Cloud Router BGP 피어를 강제 비활성화하여 AWS TGW에서 GCP 경로(10.50.0.0/24)가 철회되고, forecast-svc → PSC(10.50.0.10) → BigQuery 연결이 불가해지는 장애 시나리오를 재현한다. BGP 재활성화 후 경로 복원 및 연결 회복을 검증한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| GCP HA VPN | `bookflow-aws-tunnel-tunnel0/1` 양쪽 UP |
| GCP Cloud Router | `bookflow-aws-cr` BGP 피어 2개 활성화 상태 |
| AWS VPN | `bookflow-vpn-gcp` VgwTelemetry UP (AWS CGW IP: `34.157.64.22`) |
| TGW 라우트 테이블 | GCP PSC 경로 `10.50.0.0/24` 수신 상태 |
| forecast-svc | `bookflow` 네임스페이스 `1/1 Running` |
| gcloud | GCP 인증 완료 (`bookflow` 프로젝트) |
| aws CLI | `ap-northeast-1` 인증 완료 |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| BGP 비활성화 후 AWS 터널 DOWN | `VgwTelemetry.Status = DOWN` 확인 (120s 이내) |
| TGW GCP 경로 철회 | `10.50.0.0/24` 라우트 없음 |
| forecast-svc 오류 발생 | 로그에서 `error\|timeout\|bigquery` 키워드 확인 |
| BGP 재활성화 후 터널 UP 복구 | `VgwTelemetry.Status = UP` (180s 이내) |
| TGW 경로 복원 | `10.50.0.0/24` 라우트 재수신 |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| D-1 | `check` 모드로 GCP 터널 상태, AWS VPN 터널 상태, TGW 경로 현황 확인 | 터미널 상태 테이블 모두 정상(UP) 출력 확인 |
| D-2 | `simulate` — BGP 피어(`bookflow-aws-bgp-tunnel0/1`) 강제 비활성화 | `gcloud compute routers update-bgp-peer` 완료 메시지 |
| D-3 | AWS VPN `VgwTelemetry.Status` DOWN 전환 대기 (120s 이내) | 터미널 폴링 루프에서 `DOWN 확인` 출력 |
| D-4 | TGW 라우트 테이블에서 `10.50.0.0/24` 경로 철회 확인 | `aws ec2 search-transit-gateway-routes` 결과 0건 |
| D-5 | forecast-svc 파드 로그에서 BigQuery 연결 오류 탐지 | `kubectl logs` 필터링 결과 `error/timeout/bigquery` 키워드 1건 이상 |
| D-6 | `restore` — BGP 피어 재활성화 → 터널 UP 복구 대기 | `VgwTelemetry.Status = UP` 전환 확인 (180s 이내) |
| D-7 | 복구 후 TGW `10.50.0.0/24` 경로 재수신 확인 | `search-transit-gateway-routes` 결과 1건 이상 |
| D-8 | `all` 모드로 전체 시나리오 자동 실행 및 최종 상태 출력 | 요약 테이블 모든 항목 정상 |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| D-1 | 터미널 전체 — `bash gcp-vpn-failure-test.sh all` | 터미널 전체 화면 녹화 |
| D-2 | BGP 비활성화 구간 — `[STEP] BGP 피어 비활성화` 이후 DOWN 감지 | 녹화 중 타임스탬프 메모 |
| D-3 | TGW 경로 철회 확인 출력 + forecast-svc 오류 로그 | 터미널 스크롤 캡처 |
| D-4 | 복구 후 최종 상태 테이블 (터널 UP · 경로 복원) | 터미널 정지 화면 스크린샷 |
| D-5 | AWS Console → VPN Connections → Tunnel Telemetry (DOWN→UP 전이) | 브라우저 화면 스크린샷 |

---

## Scenario E — Azure-AWS VPN Active/Standby Failover (`vpn-failover-test.sh`)

### 검증 목표

Azure-AWS 간 Site-to-Site VPN의 Tunnel1(Active)을 PSK 변경으로 강제 다운시키고, Tunnel2(Standby)가 Active로 자동 전환되어 AWS-Azure 통신이 중단 없이 유지되는지 검증한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| AWS VPN | `vpn-0c5c1f736a382cd41` Tunnel1 · Tunnel2 모두 UP |
| Azure VPN 연결 | `conn-bookflowmj-aws-active` · `conn-bookflowmj-aws-standby` 연결 상태 |
| TGW 라우트 | Azure VNet(`172.16.0.0/16`) 경로 Tunnel1 경유 수신 중 |
| EKS | `notification-svc` 파드 Running (AWS→Azure 통신 검증용) |
| aws CLI · az CLI | 각각 `ap-northeast-1` · 로그인 완료 상태 |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| Tunnel1 DOWN | PSK 변경 후 `VgwTelemetry DOWN` 확인 |
| Tunnel2 BGP 수신 | `BGP ROUTES > 0` 전환 (180s 이내) |
| TGW Azure 경로 유지 | `172.16.0.0/16` 경로 Tunnel2 경유 유지 |
| AWS-Azure 통신 | EKS 파드 → `172.16.1.1:443` TCP 연결 성공 |
| Tunnel1 복구 | PSK 원복 후 `UP` 복구 (120s 이내) |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| E-1 | `check` — Tunnel1/Tunnel2 상태, BGP, TGW Azure 경로 확인 | 터미널 테이블 Tunnel1 Active · Tunnel2 Standby 출력 |
| E-2 | `failover` — Azure `conn-bookflowmj-aws-active` PSK 임시값 변경 | `az network vpn-connection update` 완료 |
| E-3 | Tunnel1 DOWN 대기 (폴링 루프, 최대 180s) | `[Xs] Tunnel1: DOWN` 출력 확인 |
| E-4 | Tunnel2 BGP ROUTES > 0 전환 대기 | `Failover 완료` 출력 확인 |
| E-5 | `verify` — TGW Azure 경로 Tunnel2 경유 확인 | `search-transit-gateway-routes` 결과 VPN Conn ID 포함 |
| E-6 | `verify` — EKS 파드 → Azure `172.16.1.1:443` TCP 연결 테스트 | `TCP REACHABLE` 출력 |
| E-7 | `restore` — PSK 원복 → Tunnel1 UP 복구 대기 (120s) | `Tunnel1 복구 완료` 출력 |
| E-8 | `all` 모드로 전체 시나리오 실행 (failover → verify → restore) | 최종 Tunnel1/2 모두 UP 출력 |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| E-1 | 터미널 전체 — `bash vpn-failover-test.sh all` | 터미널 전체 화면 녹화 |
| E-2 | Tunnel1 DOWN 폴링 구간 → `DOWN 확인` 출력 | 녹화 중 타임스탬프 메모 |
| E-3 | Failover 완료 출력 (`Tunnel2 BGP ROUTES 수신`) + TGW 경로 확인 | 터미널 스크롤 캡처 |
| E-4 | verify 결과 — TCP REACHABLE 출력 + 최종 터널 상태 테이블 | 터미널 정지 화면 스크린샷 |
| E-5 | AWS Console → VPN Connections → Tunnel Telemetry 전이 (Tunnel2 UP) | 브라우저 화면 스크린샷 |

---

## Scenario F — Client VPN 비인가 인증키 침입 탐지 (`client-vpn-cert-intrusion-test.sh`)

### 검증 목표

각 데스크탑에 발급된 고유 클라이언트 인증서(CN=bookflow-desktop-{1,2,3})를 기반으로, 등록되지 않은 CN의 인증서가 Client VPN 접속을 시도할 때 탐지 → CRL(Certificate Revocation List) 등록 → Client VPN 엔드포인트 임포트를 통해 이후 접속을 TLS 단계에서 차단하는 보안 시나리오를 검증한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| Client VPN 엔드포인트 | CloudFormation `bookflow-client-vpn` 스택 배포 완료 (뮤추얼 TLS 인증) |
| CA 인증서/키 | `~/bookflow-vpn-certs/ca.crt` · `ca.key` 배치 완료 |
| 데스크탑 인증서 | `~/bookflow-vpn-certs/desktop-{1,2,3}.crt` 배치 완료 |
| aws CLI | `ap-northeast-1` Client VPN 관리 권한 |
| openssl · python3 | 로컬 설치 완료 |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| 비인가 인증서 생성 | CA로 서명된 `bookflow-desktop-attacker` CN 인증서 생성 |
| CRL 등록 | OpenSSL CA DB에 폐기 처리 + CRL 파일 생성 |
| Client VPN CRL 임포트 | `aws ec2 import-client-vpn-client-certificate-revocation-list` 성공 |
| 활성 세션 종료 | `terminate-client-vpn-connections` 비인가 CN 세션 0건 |
| CRL 유효성 | OpenSSL CA 서명 검증 통과 |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| F-1 | `check` — 활성 VPN 연결 목록 및 등록/비등록 CN 하이라이트 | 등록된 CN(desktop-1,2,3)은 ✓, 비인가는 ⚠ 표시 |
| F-2 | `simulate` — CA로 서명된 비인가 인증서(`bookflow-desktop-attacker`) 생성 | 인증서 파일 생성 + Serial 출력 확인 |
| F-3 | `simulate` — CloudWatch Logs 최근 연결 이벤트에서 미등록 CN 탐색 | 실제 미등록 CN 있으면 추가 표시, 없으면 시뮬레이션 CN 탐지 출력 |
| F-4 | `revoke` — OpenSSL CA DB에 비인가 인증서 CRL 등록 | `CRL 등록 완료 (Serial: XXX)` 출력 + CRL 파일 생성 확인 |
| F-5 | `revoke` — CRL → Client VPN 엔드포인트 임포트 | `aws ec2 import-client-vpn-client-certificate-revocation-list` 성공 출력 |
| F-6 | `revoke` — 비인가 CN 활성 세션 강제 종료 | `활성 세션 없음` 또는 세션 종료 완료 출력 |
| F-7 | `verify` — CRL 파일 내 폐기 Serial 확인 | `폐기된 인증서: N개` + Serial 일치 |
| F-8 | `verify` — 활성 연결에 비인가 CN 잔류 여부 확인 | `활성 세션 없음` 출력 (차단 정상) |
| F-9 | `restore` — 빈 CRL 임포트로 폐기 목록 초기화 + 시뮬레이션 파일 삭제 | CRL 파일 삭제 완료 출력 |
| F-10 | `all` 모드로 전체 시나리오 실행 | 최종 `check` 에서 CRL 없음 + 등록 데스크탑 정상 출력 |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| F-1 | 터미널 전체 — `bash client-vpn-cert-intrusion-test.sh all` | 터미널 전체 화면 녹화 |
| F-2 | 침입 탐지 박스 출력 (`[침입 탐지] 비인가 인증서 접속 시도 감지`) | 터미널 정지 화면 스크린샷 |
| F-3 | `[폐기 완료]` 박스 출력 (CN·Serial·CRL 폐기 총 수) | 터미널 정지 화면 스크린샷 |
| F-4 | verify 결과 — CRL Serial + `활성 세션 없음` 출력 | 터미널 스크롤 캡처 |
| F-5 | AWS Console → Client VPN → Certificate Revocation List 탭 | 브라우저 화면 스크린샷 |

---

## Scenario G — GCP BigQuery + Cloud Functions 부하 테스트 (`gcp_bq_load_cf_test.py`)

### 검증 목표

GCS 스테이징 버킷에 배치 파일을 업로드하면 Cloud Functions(Eventarc 트리거)가 BigQuery 테이블에 로드 작업을 수행하는 파이프라인을 검증한다. 동시 다중 업로드 시 Cloud Functions 동시성 처리 및 BigQuery 로드 완료 정확성을 확인한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| GCP Cloud Functions | `99-content-runtime/functions.tf` 배포 완료 (load-from-gcs 함수) |
| BigQuery 데이터셋 | `bookflow` 데이터셋 테이블 접근 가능 |
| GCS 버킷 | 스테이징 버킷 쓰기 권한 |
| Eventarc 트리거 | GCS → Cloud Functions 트리거 활성화 |
| gcloud · python3 | 인증 완료 + `google-cloud-bigquery` 패키지 설치 |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| Cloud Functions 트리거 | GCS 업로드 후 60s 이내 함수 호출 로그 확인 |
| BigQuery 로드 완료 | `bq_load_job.state == DONE` (오류 없음) |
| 동시 업로드 처리 | N개 파일 업로드 시 N개 BQ 로드 성공 (누락/중복 0) |
| 오류율 | BQ 로드 실패 0건 |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| G-1 | GCS 스테이징 버킷에 테스트 CSV 파일 단건 업로드 | `gsutil cp` 성공 메시지 |
| G-2 | Cloud Functions 실행 로그 확인 (Eventarc 트리거 → 함수 호출) | `gcloud functions logs read` 에서 실행 기록 확인 |
| G-3 | BigQuery 로드 완료 및 행 수 일치 확인 | `bq query` 로 삽입된 행 수 = 원본 파일 행 수 |
| G-4 | 동시 N개 파일 업로드 → 모두 BQ 로드 완료 확인 | BQ 테이블 전체 행 수 누적 일치 |
| G-5 | GCS 스테이징 정리 함수(`gcs-staging-cleanup`) 실행 확인 | 스테이징 버킷 파일 삭제 여부 확인 |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| G-1 | 터미널 — `python3 gcp_bq_load_cf_test.py` 실행 화면 | 터미널 전체 화면 녹화 |
| G-2 | Cloud Functions 호출 로그 (`gcloud functions logs read`) | 터미널 정지 화면 스크린샷 |
| G-3 | BigQuery 행 수 일치 확인 출력 | 터미널 정지 화면 스크린샷 |
| G-4 | GCP Console → Cloud Functions → 실행 기록 (N건 성공) | 브라우저 화면 스크린샷 |

---

## Scenario H — Publisher ASG 자동 스케일링 (`publisher_asg_test.py`)

### 검증 목표

Publisher EC2 Auto Scaling Group에 부하를 발생시켜 ASG가 scale-out하고, 부하 해소 후 scale-in이 정상적으로 수행되는지 검증한다. CodeDeploy를 통해 신규 인스턴스에 자동으로 배포가 완료되는지도 확인한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| Publisher ASG | `bookflow-publisher-asg` Min:1 / Max:3 / Desired:1 활성 상태 |
| CloudWatch 알람 | CPU/요청 기반 scale-out 정책 활성화 |
| CodeDeploy | `publisher-codedeploy` 배포 그룹 설정 완료 |
| Publisher 백엔드 | FastAPI 앱 실행 중 (`/health` 200 OK) |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| Scale-out 트리거 | 부하 발생 후 300s 이내 Desired 증가 확인 |
| 신규 인스턴스 InService | 프로비저닝 후 120s 이내 `InService` |
| CodeDeploy 자동 배포 | 신규 인스턴스에 Publisher 앱 배포 완료 |
| Scale-in | 부하 해소 후 Desired 원복 |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| H-1 | 현재 ASG Desired/Min/Max 및 인스턴스 상태 확인 | `aws autoscaling describe-auto-scaling-groups` 출력 |
| H-2 | 부하 발생 → CloudWatch 알람 ALARM 전환 확인 | `aws cloudwatch describe-alarms` 상태 ALARM |
| H-3 | ASG Desired 증가 감지 (scale-out 트리거) | Desired 변경 로그 출력 |
| H-4 | 신규 인스턴스 `InService` 전환 대기 | `aws autoscaling describe-auto-scaling-instances` InService 확인 |
| H-5 | 신규 인스턴스 `/health` 엔드포인트 응답 확인 | HTTP 200 OK 응답 |
| H-6 | 부하 해소 → scale-in → Desired 원복 확인 | Desired 감소 및 인스턴스 `Terminating` 확인 |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| H-1 | 터미널 — `python3 publisher_asg_test.py` 실행 화면 | 터미널 전체 화면 녹화 |
| H-2 | ASG Desired 변경 감지 출력 | 터미널 스크롤 캡처 |
| H-3 | AWS Console → Auto Scaling Groups → 인스턴스 수 변화 | 브라우저 화면 스크린샷 |
| H-4 | AWS Console → CloudWatch → 알람 상태 전이 (OK → ALARM → OK) | 브라우저 화면 스크린샷 |

---

## Scenario I — Decision Service 4-Stage Cascade 의사결정 (신규)

### 검증 목표

`decision-svc /decision/decide` API가 재고 상황에 따라 Stage 0(REBALANCE) → Stage 1(WH_TO_STORE) → Stage 2(WH_TRANSFER) → Stage 3(PUBLISHER_ORDER) 순서로 cascade 결정을 수행하는지, EOQ 계산·urgency 자동 산정·auto_execute_eligible 설정이 올바르게 동작하는지 검증한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| decision-svc | `bookflow` 네임스페이스 `1/1 Running` |
| RDS | `pending_orders`, `inventory`, `forecast_cache`, `locations`, `books` 테이블 접근 가능 |
| 권한 | `hq-admin` 또는 `wh-manager` JWT 토큰 유효 |
| notification-svc | `order.pending` Redis publish 수신 가능 |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| Stage 0 (REBALANCE) | 동일 WH 내 여유 매장 존재 시 `order_type=REBALANCE` 반환 |
| Stage 1 (WH_TO_STORE) | WH 본체 여유 존재 시 `order_type=WH_TO_STORE` 반환 |
| Stage 2 (WH_TRANSFER) | 타 권역 WH 여유 존재 시 `order_type=WH_TRANSFER` 반환 |
| Stage 3 (PUBLISHER_ORDER) | 모든 내부 재고 부족 시 `order_type=PUBLISHER_ORDER` 반환 |
| EOQ 적용 | Stage 3 시 `qty ≥ max(EOQ, 요청량)` 확인 |
| urgency 자동 산정 | `stock_days_remaining < 1.0` → URGENT, `< 0.5` → CRITICAL |
| auto_execute_eligible | Stage 3 + URGENT/CRITICAL → `true` |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| I-1 | Stage 0 — 동일 WH 내 여유 매장에서 REBALANCE 결정 호출 | 응답 `stage=0, order_type=REBALANCE` 확인 |
| I-2 | Stage 1 — 동일 WH 본체 여유로 WH_TO_STORE 결정 호출 | 응답 `stage=1, order_type=WH_TO_STORE` 확인 |
| I-3 | Stage 2 — 타 권역 WH 여유로 WH_TRANSFER 결정 호출 | 응답 `stage=2, order_type=WH_TRANSFER, partner_surplus > 0` |
| I-4 | Stage 3 — 모든 재고 소진 시 PUBLISHER_ORDER 결정 호출 | 응답 `stage=3, order_type=PUBLISHER_ORDER` |
| I-5 | Stage 3 EOQ 검증 — 요청 qty < EOQ 시 final_qty = EOQ 확인 | `rationale.eoq_calc ≤ rationale.final_qty` |
| I-6 | urgency CRITICAL — stock_days_remaining < 0.5 설정 후 호출 | 응답 `urgency_level=CRITICAL, auto_execute_eligible=true` |
| I-7 | SOFT_DISCONTINUE 도서 Stage 3 차단 확인 | HTTP 400 응답 + `신규 출판사 발주 불가` 메시지 |
| I-8 | `/decision/decide/batch` — N건 일괄 결정 호출 | 응답 `total=N, failed=0, by_stage 분포 확인` |
| I-9 | `/decision/plan-daily` — 일괄 익일 배치 발의 | 응답 `rows_created > 0, by_stage 분포 출력` |
| I-10 | `plan-daily` 이후 `ForecastCompleted` 알림 발송 확인 | `notifications_log` 에서 `event_type=ForecastCompleted` 1건 |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| I-1 | Stage 별 API 응답 터미널 출력 (curl/httpx 결과) | 터미널 정지 화면 스크린샷 4종 (Stage 0~3) |
| I-2 | Stage 3 EOQ 계산 rationale JSON 상세 출력 | 터미널 스크롤 캡처 |
| I-3 | CRITICAL urgency + auto_execute_eligible=true 응답 | 터미널 정지 화면 스크린샷 |
| I-4 | plan-daily 결과 — `rows_created, by_stage` 출력 | 터미널 정지 화면 스크린샷 |
| I-5 | RDS pending_orders 테이블 — 생성된 발주 행 확인 | DB 조회 출력 스크린샷 |

---

## Scenario J — Intervention Service 주문 State Machine (`intervention-svc`)

### 검증 목표

주문 4-step state machine(PENDING → APPROVED → IN_TRANSIT → EXECUTED, any → REJECTED)이 권한 매트릭스(hq-admin/wh-manager/branch-clerk)에 따라 올바르게 전환되고, REJECTED 시 IN_TRANSIT 재고 복원·chained WH_TO_STORE cascade 취소가 원자적으로 수행되는지 검증한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| intervention-svc | `bookflow` 네임스페이스 `1/1 Running` |
| inventory-svc | `/inventory/adjust` 호출 가능 (동일 네임스페이스) |
| RDS | `pending_orders`, `order_approvals`, `inventory`, `audit_log` 테이블 |
| 역할별 JWT | `hq-admin`, `wh-manager(wh_id=1)`, `branch-clerk(store_id=1)` 토큰 발급 |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| PENDING → APPROVED (양측 ✓) | SOURCE + TARGET 동의 후 status=APPROVED |
| APPROVED → IN_TRANSIT | source inventory -qty 차감 확인 |
| IN_TRANSIT → EXECUTED | target inventory +qty 적재 확인 |
| REJECTED (IN_TRANSIT) | source inventory +qty 복원 확인 |
| Cascade 취소 | WH_TRANSFER/PUBLISHER 거부 시 chained WH_TO_STORE 연쇄 REJECTED |
| Race-safe | 동시 양측 승인 요청 ON CONFLICT 처리 (중복 전환 없음) |
| hq-admin escalation | BOTH side 자동 삽입으로 단독 승인 완료 |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| J-1 | WH_TO_STORE SOURCE 승인 → TARGET 승인 → APPROVED 전환 확인 | 첫 승인: `transitioned=false`, 두 번째: `transitioned=true` |
| J-2 | APPROVED → IN_TRANSIT 전환 + source inventory -qty | inventory-svc `on_hand` 감소 확인 |
| J-3 | IN_TRANSIT → EXECUTED 전환 + target inventory +qty | inventory-svc `on_hand` 증가 확인 |
| J-4 | IN_TRANSIT 상태 REJECTED → source inventory +qty 복원 | inventory `on_hand` = 원래값 |
| J-5 | WH_TRANSFER APPROVED → 거부 시 chained WH_TO_STORE cascade REJECTED | DB에서 chained row status=REJECTED 확인 |
| J-6 | hq-admin BOTH escalation — 단독 승인으로 APPROVED 전환 | `side=BOTH, transitioned=true` 응답 |
| J-7 | branch-clerk 권한 밖 주문 승인 시도 → 403 차단 | HTTP 403 + `자기 매장 외 권한 없음` |
| J-8 | 이미 EXECUTED 주문에 REJECTED 시도 → 409 차단 | HTTP 409 + `already finalized` |
| J-9 | 07:00 CronJob auto_execute — auto_execute_eligible=true 주문 자동 실행 | audit_log `order.dispatch.publisher_auto` 이벤트 확인 |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| J-1 | 4-step 전환 API 응답 순서 출력 | 터미널 정지 화면 스크린샷 |
| J-2 | IN_TRANSIT/EXECUTED inventory 변화 (before/after) | DB 조회 전후 비교 스크린샷 |
| J-3 | REJECTED + IN_TRANSIT 재고 복원 출력 | 터미널 정지 화면 스크린샷 |
| J-4 | Cascade cancel chained_ids 응답 JSON | 터미널 정지 화면 스크린샷 |
| J-5 | audit_log 이벤트 누적 (approve → dispatch → receive) | DB 조회 출력 스크린샷 |

---

## Scenario K — Notification Service 이벤트 라우팅 + Deduplication (`notification-svc`)

### 검증 목표

`notification-svc /notification/send`가 event_type별로 Logic Apps 워크플로(approval-request/notification/stock-depart/stock-arrival)에 올바르게 라우팅되고, Redis 기반 5분 중복 발송 차단(dedup)이 동작하며, Redis pub/sub 채널(order.pending/order.approved 등 8채널)로 실시간 브로드캐스트되는지 검증한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| notification-svc | `1/1 Running`, Logic Apps URL 4종 Secret 설정 완료 |
| Redis | `notification-svc` Redis 연결 정상 |
| RDS | `notifications_log` 테이블 접근 가능 |
| Logic Apps | `la-bookflowmj-approval-request`, `notification`, `stock-depart`, `stock-arrival` 활성화 |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| OrderPending 라우팅 | `approval_request` Logic Apps 호출 + `order.pending` Redis pub |
| SpikeUrgent 라우팅 | `notification` Logic Apps 호출 + `spike.detected` Redis pub |
| StockDepartPending 라우팅 | `stock_depart` Logic Apps 호출 + `order.dispatched` Redis pub |
| Deduplication | 동일 event_type + correlation_id 5분 내 재발송 → status=DEDUP |
| InboundRejected 버퍼링 | Redis `inbound_rejected_buffer:{wh_id}` RPUSH 처리 |
| notifications_log 기록 | 모든 발송 이벤트 RDS 저장 확인 |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| K-1 | `OrderPending` 전송 → Logic Apps `approval-request` 호출 확인 | `notifications_log status=SENT` + Logic Apps 실행 기록 |
| K-2 | `SpikeUrgent` 전송 → Logic Apps `notification` 호출 확인 | Logic Apps 실행 기록 확인 |
| K-3 | `StockDepartPending` 전송 → `stock-depart` Logic Apps 호출 | Logic Apps `la-bookflowmj-stock-depart` 실행 기록 |
| K-4 | 동일 correlation_id 5분 내 재전송 → `status=DEDUP` 반환 | 응답 `status=DEDUP` + Redis KEY TTL 확인 |
| K-5 | Redis pub/sub — `order.pending` 채널 수신 확인 | `redis-cli SUBSCRIBE order.pending` 메시지 수신 |
| K-6 | `InboundRejected` → Redis 버퍼(`inbound_rejected_buffer:{wh_id}`) 적재 | Redis LLEN 증가 확인 |
| K-7 | `BranchFeedback` — branch-clerk 권한으로 피드백 제출 | `notifications_log event_type=BranchFeedback` 저장 확인 |
| K-8 | hq-admin 이외 역할 `BranchFeedback` 제출 → 403 차단 | HTTP 403 응답 |
| K-9 | `GET /notification/recent` — 최근 50건 조회 | 발송된 이벤트 목록 포함 응답 |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| K-1 | API 호출 + Logic Apps 실행 기록 나란히 비교 | 터미널 + Azure Portal 화면 분할 스크린샷 |
| K-2 | DEDUP 응답 출력 (`status=DEDUP`) | 터미널 정지 화면 스크린샷 |
| K-3 | Redis pub/sub 실시간 메시지 수신 | 터미널 SUBSCRIBE 창 스크린샷 |
| K-4 | notifications_log 최근 N건 이벤트 테이블 | DB 조회 출력 스크린샷 |

---

## Scenario L — Inventory CronJob 예약 재고 정리 (`inventory-svc`)

### 검증 목표

`inventory-svc` CronJob(`cronjob-reservation-cleanup.yaml`)이 만료된 예약 재고(reserved_qty)를 자동으로 정리하고, 재고 수치가 올바르게 반영되며 `stock.changed` Redis 이벤트가 발행되는지 검증한다.

### 전제조건

| 항목 | 조건 |
|------|------|
| inventory-svc | `bookflow` 네임스페이스 `1/1 Running` |
| CronJob | `k8s/cronjob-reservation-cleanup.yaml` 배포 완료 |
| RDS | `inventory` 테이블 `reserved_qty > 0`, 만료 예약 데이터 존재 |
| Redis | `stock.changed` 채널 구독 가능 |

### 성공 기준

| 항목 | 기준값 |
|------|--------|
| 만료 예약 정리 | CronJob 실행 후 만료된 `reserved_qty` = 0 |
| stock.changed 발행 | 정리된 각 (isbn13, location_id)마다 Redis 이벤트 발행 |
| 정상 예약 보존 | 만료되지 않은 예약은 변경 없음 |
| CronJob 완료 | `kubectl get job` status `Complete` |

### 테스트

| 번호 | 테스트 내용 | 검증 방법 |
|------|-------------|-----------|
| L-1 | 만료 예약 데이터 삽입 (RDS INSERT) | DB 삽입 확인 |
| L-2 | CronJob 수동 트리거 (`kubectl create job --from`) | Job Pod `Running → Completed` |
| L-3 | 만료된 `reserved_qty` 0으로 정리 확인 | `SELECT reserved_qty FROM inventory WHERE ...` = 0 |
| L-4 | 만료되지 않은 예약은 유지 확인 | 비만료 예약 `reserved_qty` 변경 없음 |
| L-5 | `stock.changed` Redis 이벤트 발행 확인 | `redis-cli SUBSCRIBE stock.changed` 메시지 수신 |
| L-6 | audit_log 정리 이벤트 기록 확인 | `audit_log` 에서 `reservation.cleanup` 액션 확인 |

### 촬영 방식

| 번호 | 촬영 대상 | 녹화 방법 |
|------|-----------|-----------|
| L-1 | kubectl CronJob 트리거 → Pod Completed 전이 | 터미널 정지 화면 스크린샷 |
| L-2 | DB before/after `reserved_qty` 비교 | DB 조회 전후 비교 스크린샷 |
| L-3 | Redis `stock.changed` 이벤트 수신 | 터미널 SUBSCRIBE 창 스크린샷 |
