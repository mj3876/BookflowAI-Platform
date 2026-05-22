# BookFlow Client VPN 설정 가이드

AWS VPC 내부망(`10.0.0.0/16`)에 접속하기 위한 Client VPN 설정입니다.  
아래 순서대로 따라하면 **약 5분** 안에 완료됩니다.

---

## 전제조건

| 항목 | 설치 방법 |
|---|---|
| AWS CLI v2 | [설치 가이드](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) |
| AWS 계정 인증 | 팀장에게 Access Key 또는 SSO 링크 요청 |
| openssl | macOS: 기본 설치됨 / Ubuntu: `sudo apt install openssl` / Windows: Git Bash 또는 WSL |

---

## Step 1 — AWS 인증 확인

```bash
aws sts get-caller-identity --region ap-northeast-1
```

정상 출력 예시:
```json
{
    "UserId": "AIDA...",
    "Account": "354493396671",
    "Arn": "arn:aws:iam::354493396671:user/minji"
}
```

오류가 나면 `aws configure`로 Access Key를 먼저 등록하세요.

---

## Step 2 — 스크립트 실행

```bash
# 리포지토리 루트에서 실행
bash scripts/setup-vpn.sh
```

이름 입력 프롬프트가 뜨면 본인 이름을 입력합니다 (예: `minji`).

스크립트가 자동으로:
1. AWS Secrets Manager에서 CA 인증서를 가져와 개인 인증서를 발급
2. `~/.bookflow-vpn/bookflow-client-<이름>.ovpn` 파일 생성

---

## Step 3 — AWS VPN Client 설치

[AWS VPN Client 다운로드](https://aws.amazon.com/vpn/client-vpn-download/)

| OS | 파일 |
|---|---|
| Windows | `.msi` 설치 파일 |
| macOS | `.pkg` 설치 파일 |
| Ubuntu/Debian | [공식 APT 가이드](https://docs.aws.amazon.com/vpn/latest/clientvpn-user/client-vpn-connect-linux.html) |

---

## Step 4 — 프로파일 등록 및 연결

1. AWS VPN Client 실행
2. **File** → **Manage Profiles** → **Add Profile**
3. `.ovpn` 파일 선택: `~/.bookflow-vpn/bookflow-client-<이름>.ovpn`
4. **Add Profile** 클릭
5. **Connect** 클릭

연결 완료 후 내부망 `10.0.0.0/16` 접근이 가능합니다.

---

## 접속 가능한 내부 리소스

| 리소스 | 용도 |
|---|---|
| RDS PostgreSQL | 내부 DB 직접 접근 |
| ElastiCache Redis | 캐시 서버 |
| EKS API Server | kubectl 직접 사용 |
| Ansible Node (EC2) | SSH 접근 |

---

## 문제 해결

**`AWS 인증 실패` 오류**
```bash
aws configure
# AWS Access Key ID, Secret, Region(ap-northeast-1) 입력
```

**`CA 시크릿 조회 실패` 오류**  
IAM 권한 문제입니다. 팀장에게 `secretsmanager:GetSecretValue` on `bookflow/vpn/ca` 권한을 요청하세요.

**Windows에서 openssl 없음**  
Git Bash를 사용하세요. Git for Windows 설치 시 openssl이 함께 포함됩니다: https://git-scm.com/download/win

**이미 .ovpn 파일이 있다는 오류**  
재발급이 필요하면 `y`를 입력하거나, 기존 파일을 삭제 후 재실행하세요:
```bash
rm ~/.bookflow-vpn/bookflow-client-<이름>.ovpn
bash scripts/setup-vpn.sh
```

---

## 보안 주의사항

- `~/.bookflow-vpn/` 디렉터리는 **절대 외부에 공유하지 마세요** (개인 키 포함)
- `.ovpn` 파일에는 개인 Private Key가 포함되어 있습니다
- 분실 또는 유출 시 팀장에게 즉시 알려 인증서를 폐기합니다
