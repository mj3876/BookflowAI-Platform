# Azure · Persistent (🔒 영구 자원)

## 이 레이어의 역할

BOOKFLOW Azure 환경의 **절대 삭제하면 안 되는 영구 자원**들. 한 번 배포하면 발표일까지 유지.

- 외부 연결 식별자 (Entra ID 앱 · Client ID · Tenant ID)
- 멀티클라우드 VPN 연결점 (Azure VPN Gateway Public IP × 2)
- AWS 팀이 등록한 Customer Gateway의 peer IP 기준

**라이프사이클**: 🔒 영구 · `entra-setup.sh` 최초 1회 실행 + PIP는 `deploy-all.sh` Stack 6에서 자동 생성.

---

## 영구 자원 목록 (6개)

### 🪪 Entra ID (3개)

| 자원 | 이름 | 역할 |
|---|---|---|
| 앱 등록 | `BookFlow-Internal` | Azure-AWS 인증 브릿지 + auth-pod OIDC. App ID·Tenant ID를 AWS 팀에 전달 |
| 서비스 주체 | (앱 등록과 자동 연동) | Azure RBAC 권한 부여 대상 |
| Client Secret | Key Vault `bookflow-client-secret` (`kv-bookflowmj`) 에 저장 | AWS → Azure 인증 + auth-pod OIDC. 만료 1년 · 30일 Logic App 자동 rotate |
| Redirect URIs | `https://auth.bookflow.internal/callback` (placeholder) + `https://bookflow.duckdns.org/auth/callback` (auth-pod) | OIDC code flow callback |

### 🌐 Public IP (2개)

| 자원 | 이름 | IP 타입 | 역할 |
|---|---|---|---|
| PIP Active | `pip-bookflow-vpngw-active` | Static · Standard · Zone 1/2/3 | VPN Gateway Active 연결점. AWS CGW peer IP |
| PIP Standby | `pip-bookflow-vpngw-standby` | Static · Standard · Zone 1/2/3 | 예비 연결점 (Active-Active 확장 대비) |

### 👥 Entra ID 그룹 (4개)

| 그룹명 | 역할 |
|---|---|
| `BF-HeadQuarter` | 본사 사용자 그룹 |
| `BF-Logistics` | 물류 사용자 그룹 |
| `BF-Branch` | 지점 사용자 그룹 |
| `BF-Admin` | 관리자 그룹 |

---

## 최초 배포 방법

### 1. PIP 생성 (deploy-all.sh Stack 6에 포함)

```bash
cd bookflow-azure-iac
bash scripts/deploy-all.sh
# → STACK 6 (VPN Gateway) 배포 시 PIP 2개 자동 생성
```

### 2. Entra ID 앱 등록 (별도 수동 실행)

```bash
bash scripts/entra-setup.sh
```

완료 후 출력되는 값을 AWS 팀에 전달:
```
테넌트 ID:     xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
클라이언트 ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

---

## 배포 후 수동 작업

### AWS 팀 협력: Customer Gateway 등록

Azure PIP 확인:
```bash
az network public-ip list \
  --resource-group rg-bookflow \
  --query "[?contains(name,'vpngw')].{name:name, ip:ipAddress}" \
  --output table
```

→ `pip-bookflow-vpngw-active`의 IP를 AWS 팀에 전달 → AWS 팀이 Customer Gateway 등록.

---

## Key Vault 연계 (자동)

`entra-setup.sh` 실행 시 아래 시크릿이 `kv-bookflowmj`에 자동 저장됨 (PREFIX=`bookflowmj`).

| Secret 이름 | 내용 |
|---|---|
| `bookflow-tenant-id` | Azure 테넌트 ID |
| `bookflow-client-id` | BookFlow-Internal App ID |
| `bookflow-client-secret` | Client Secret (1년 만료) |
| `aws-api-gateway-url` | VPN 연결 후 실제 URL로 교체 예정 (현재 PLACEHOLDER) |

Key Vault soft-delete + purge protection 적용 → 리소스 그룹 삭제 시에도 90일간 시크릿 보존.

---

## 다른 레이어가 이 자원을 참조하는 방식

| 참조처 | 사용 값 | 목적 |
|---|---|---|
| `vpn-connect.sh` | `pip-bookflow-vpngw-active` IP | VPN Connection Pre-Shared Key 협의 |
| `func-bookflow-sync` | `bookflow-client-id` (Key Vault 참조) | Azure → AWS 호출 시 인증 |
| `la-bookflow-secret-rotation` | `bookflow-client-secret` | Secret 교체 워크플로 |
| AWS Customer Gateway | Active PIP IP | IPsec 터널 peer IP |

---

## 검증

```bash
# Entra 앱 존재 확인
az ad app list --display-name "BookFlow-Internal" \
  --query "[0].{appId:appId, displayName:displayName}" --output table

# PIP IP 확인
az network public-ip list \
  --resource-group rg-bookflow \
  --query "[?contains(name,'vpngw')].{name:name, ip:ipAddress}" \
  --output table

# Key Vault 시크릿 목록 확인
az keyvault secret list --vault-name kv-bookflow --output table
```

---

## 삭제 금지 사유 요약

| 자원 | 삭제 시 결과 |
|---|---|
| `BookFlow-Internal` 앱 | App ID 변경 → AWS 팀 인증 설정 전면 재협의 필요 |
| `pip-bookflow-vpngw-active` | IP 변경 → AWS Customer Gateway 재등록 필요 |
| `pip-bookflow-vpngw-standby` | IP 변경 → 향후 Active-Active 전환 시 재협의 필요 |
| Entra 그룹 | 멤버십 초기화 → 사용자 재배정 필요 |

## 비고

- PIP 2개는 VPN Gateway 삭제 후에도 **독립 리소스로 유지됨** (월 ~₩10,000)
- Entra 자원은 Azure 과금 없음
- Client Secret 만료 시 `az ad app credential reset` 후 Key Vault 재저장 필요
