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
