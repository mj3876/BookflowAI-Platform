# GCS PSC 라우트 누락 장애 대응 — 2026-05-13

---

## 에러 로그

```
An error occurred while calling o1161.parquet.
Error accessing gs://project-8ab6bf05-54d2-4f5d-b8d-bookflow-staging/mart/features.
reason=connect timed out
```

- **발생 위치**: Step Functions ETL3 → `features_build` Glue Job → GCS dual-write 단계
- **S3 primary write**: 정상 완료
- **GCS write**: connect timed out으로 실패

---

## 원인

### 네트워크 경로

```
Glue (bookflow-ai VPC) → TGW → GCP VPN → GCP PSC → storage.googleapis.com
```

### PHZ 설정

Route53 Private Hosted Zone `googleapis.com`에 다음 레코드 존재:

```
storage.googleapis.com.  A  10.50.0.10   (GCS PSC endpoint)
*.googleapis.com.        A  10.50.0.10
```

### 라우팅 불일치 (근본 원인)

| 구간 | 상태 | 내용 |
|------|------|------|
| VPC RT (bookflow-ai) | ✅ 존재 | `10.50.0.0/24 → TGW` |
| **TGW RT** | ❌ **누락** | `10.50.0.0/24 → GCP VPN attachment` 경로 없음 |

GCP VPN BGP는 `192.168.10.0/24`, `192.168.254.0/28`만 광고하고  
PSC 엔드포인트 서브넷 `10.50.0.0/24`는 광고하지 않음.  
→ TGW가 `10.50.0.10` 행 패킷을 블랙홀 처리.

### VPN 상태 (확인 결과)

```
vpn-0f3540d5d1c0c3d5c  (bookflow-vpn-gcp)
  Tunnel 1: UP   · 2 BGP routes · 13.192.13.68
  Tunnel 2: DOWN · IPSEC IS DOWN · 54.248.177.9

vpn-0d2e8ab9405122f01  (bookflow-vpn-azure)
  Tunnel 1: DOWN · IPSEC IS DOWN
  Tunnel 2: DOWN · IPSEC IS DOWN
```

GCP VPN 터널 1개는 정상. Azure VPN은 별개 이슈.

---

## 즉시 조치 (CLI)

TGW RT에 정적 라우트 수동 추가:

```bash
aws ec2 create-transit-gateway-route \
  --transit-gateway-route-table-id tgw-rtb-08ad5ea02ad85e6a6 \
  --destination-cidr-block 10.50.0.0/24 \
  --transit-gateway-attachment-id tgw-attach-0d7d4741a2002734f
```

ETL3 수동 재실행:

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:ap-northeast-1:354493396671:stateMachine:bookflow-etl3 \
  --name "manual-retry-gcs-fix-20260513"
```

→ **ETL3 SUCCEEDED** (약 7분 소요)

---

## GCS 적재 확인

```bash
gcloud storage ls -l "gs://project-8ab6bf05-54d2-4f5d-b8d-bookflow-staging/mart/features/**" \
  | sort -k2 -r | head -5
```

```
22 objects, 6.76 MiB
최종 write: 2026-05-13T03:01:17Z
파티션: feature_date=2026-02-01 ~ feature_date=2027-12-01 (21개)
```

---

## 코드 반영

### 1. `scripts/aws/tasks/cross_cloud.py`

`_attach_vpn_to_tgw_rt()` 함수 끝에 GCS PSC 정적 라우트 생성 로직 추가.

**추가 코드:**

```python
# GCS PSC 정적 라우트 — GCP VPN attachment에만 추가
# BGP가 10.50.0.0/24(PSC subnet)를 광고하지 않으므로 수동 정적 라우트 필요
# PHZ: storage.googleapis.com → 10.50.0.10 (GCS PSC endpoint)
gcs_psc_cidr = os.environ.get("BOOKFLOW_GCP_PSC_CIDR", "10.50.0.0/24")
gcp_atts = [
    a for a in attachments
    if any(t["Key"] == "Name" and "gcp" in t["Value"].lower()
           for t in a.get("Tags", []))
]
for att in gcp_atts:
    att_id = att["TransitGatewayAttachmentId"]
    try:
        ec2.create_transit_gateway_route(
            TransitGatewayRouteTableId=tgw_rt_id,
            DestinationCidrBlock=gcs_psc_cidr,
            TransitGatewayAttachmentId=att_id,
        )
        log.info(f"  GCS PSC static route {gcs_psc_cidr} → {att_id}")
    except ec2.exceptions.ClientError as e:
        if "RouteAlreadyExists" in str(e) or "already exists" in str(e).lower():
            log.info(f"  GCS PSC static route {gcs_psc_cidr} already exists · skip")
        else:
            log.warn(f"  GCS PSC static route fail: {e}")
```

**위치**: `_attach_vpn_to_tgw_rt()` 내 propagation 루프 직후

---

### 2. `infra/aws/60-network-cross-cloud/tgw-vpc-routes.yaml`

`GcpPscCidr` 파라미터 추가 + 7개 VPC RT 전체에 경로 추가.

**파라미터 추가:**

```yaml
GcpPscCidr:
  Type: String
  Default: 10.50.0.0/24
  Description: GCP PSC endpoint CIDR (PHZ storage.googleapis.com → 10.50.0.10)
```

**각 VPC RT에 추가된 리소스 (패턴 동일, 7개):**

```yaml
Rt<VpcName>ToGcpPsc:
  Type: AWS::EC2::Route
  Properties:
    RouteTableId: !ImportValue { Fn::Sub: '${ProjectName}-rt-<vpc>-private' }
    DestinationCidrBlock: !Ref GcpPscCidr
    TransitGatewayId: !ImportValue { Fn::Sub: '${ProjectName}-tgw-id' }
```

대상 VPC RT: `bookflow-ai`, `sales-data`, `egress`, `data-private`, `data-db`, `ansible-private`, `ansible-public`

---

## 배포 시 주의사항

`bookflow-ai` VPC RT의 `10.50.0.0/24 → TGW` 경로는 이미 수동으로 존재.  
CFN 업데이트 시 `RtBookflowAiToGcpPsc` 생성 충돌 발생.

**해결 방법 (택 1):**

1. 기존 라우트 삭제 후 CFN 업데이트 (짧은 단절)
2. `aws cloudformation import-resources`로 기존 리소스 CFN 편입 (무중단)

나머지 6개 VPC RT는 충돌 없이 바로 배포 가능.

---

## 커밋

| 레포 | 브랜치 | 커밋 | 내용 |
|------|--------|------|------|
| BookFlowAI-Platform | `azure` | `a4f6e54` | fix(network): GCS PSC 정적 라우트 누락 코드 반영 |
