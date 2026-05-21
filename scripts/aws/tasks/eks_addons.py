"""task-eks-addons · helm install/upgrade for ingress-nginx + cert-manager + DuckDNS webhook.

Idempotent for daily destroy/redeploy:
  - helm upgrade --install (matches existing release or creates new)
  - kubectl apply for manifests in Apps repo (eks-pods/auth-pod/k8s + dashboard-svc/k8s + duckdns-sync/k8s)

Env vars (from scripts/aws/config/.env.local):
  - BOOKFLOW_DUCKDNS_TOKEN: DuckDNS API token (required for webhook + Secret)
  - BOOKFLOW_DUCKDNS_DOMAIN: DuckDNS domain (default 'bookflow')

Sequence:
  1. helm install ingress-nginx (NLB)
  2. helm install cert-manager (with --dns01-recursive-nameservers flag)
  3. helm install cert-manager-webhook-duckdns (mmontes11 fork)
  4. kubectl create Secret duckdns-token (ingress-nginx ns)
  5. kubectl apply ClusterIssuer + Certificate (Apps repo)
  6. kubectl apply duckdns-sync CronJob (Apps repo)
  7. kubectl apply dashboard-svc Ingress (Apps repo)
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..lib import log

CERT_MANAGER_VERSION = "v1.16.1"
INGRESS_NGINX_VERSION = "4.11.3"
WEBHOOK_DUCKDNS_VERSION = "v1.2.3"
EXTERNAL_SECRETS_VERSION = "0.10.4"
PROMETHEUS_VERSION = "25.27.0"
GRAFANA_VERSION = "8.5.1"
BLACKBOX_EXPORTER_VERSION = "9.0.1"

DEFAULT_APPS_DIR_REL = "../BookFlowAI-Apps"


def _apps_dir() -> Path:
    apps_env = os.environ.get("BOOKFLOW_APPS_DIR")
    if apps_env:
        return Path(apps_env).expanduser()
    platform_root = Path(__file__).resolve().parents[3]
    return platform_root.parent / "BookFlowAI-Apps"


def _ensure_tools() -> None:
    for tool in ("helm", "kubectl", "aws"):
        if shutil.which(tool) is None:
            log.err(f"required tool not on PATH: {tool}")
            raise SystemExit(1)


def _ensure_kubeconfig() -> None:
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    log.info(f"updating kubeconfig for bookflow-eks ({region})")
    subprocess.run(
        ["aws", "eks", "update-kubeconfig", "--name", "bookflow-eks", "--region", region],
        check=True,
    )


def _helm_repo_add() -> None:
    repos = [
        ("ingress-nginx", "https://kubernetes.github.io/ingress-nginx"),
        ("jetstack", "https://charts.jetstack.io"),
        ("mmontes11", "https://mmontes11.github.io/charts"),
        ("external-secrets", "https://charts.external-secrets.io"),
        ("prometheus-community", "https://prometheus-community.github.io/helm-charts"),
        ("grafana", "https://grafana.github.io/helm-charts"),
    ]
    for name, url in repos:
        subprocess.run(["helm", "repo", "add", name, url], check=False)
    subprocess.run(["helm", "repo", "update"], check=True)


def _helm_install_ingress_nginx() -> None:
    log.info("helm upgrade --install ingress-nginx")
    subprocess.run([
        "helm", "upgrade", "--install", "ingress-nginx", "ingress-nginx/ingress-nginx",
        "--namespace", "ingress-nginx", "--create-namespace",
        "--version", INGRESS_NGINX_VERSION,
        "--set", "controller.service.type=LoadBalancer",
        "--set", "controller.service.annotations.service\\.beta\\.kubernetes\\.io/aws-load-balancer-type=nlb",
        "--wait", "--timeout", "5m",
    ], check=True)


def _helm_install_cert_manager() -> None:
    """cert-manager + ServiceAccount IRSA (Route53 DNS-01).
    eks-cert-manager-irsa stack 의 CertManagerRoute53RoleArn export 사용 → 매일 OIDC 갱신 자동 sync.
    """
    import boto3
    cf = boto3.client("cloudformation", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
    cert_role_arn = next(
        (e["Value"] for e in cf.list_exports()["Exports"] if e["Name"] == "bookflow-cert-manager-route53-role-arn"),
        None,
    )
    if not cert_role_arn:
        log.err("bookflow-cert-manager-route53-role-arn export missing · eks-cert-manager-irsa stack 먼저 deploy")
        raise SystemExit(1)
    log.info(f"helm upgrade --install cert-manager · IRSA={cert_role_arn}")
    subprocess.run([
        "helm", "upgrade", "--install", "cert-manager", "jetstack/cert-manager",
        "--namespace", "cert-manager", "--create-namespace",
        "--version", CERT_MANAGER_VERSION,
        "--set", "crds.enabled=true",
        "--set-string", f"serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn={cert_role_arn}",
        "--wait", "--timeout", "5m",
    ], check=True)


def _helm_install_external_secrets() -> None:
    """ESO 설치 + ServiceAccount 에 IRSA role ARN annotation.
    eks-eso-irsa stack 의 EsoRoleArn export 를 사용 → ESO Pod 가 Secrets Manager 접근.
    """
    import boto3
    cf = boto3.client("cloudformation", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
    eso_role_arn = next(
        (e["Value"] for e in cf.list_exports()["Exports"] if e["Name"] == "bookflow-eso-role-arn"),
        None,
    )
    if not eso_role_arn:
        log.err("bookflow-eso-role-arn export missing · eks-eso-irsa stack 먼저 deploy")
        raise SystemExit(1)
    log.info(f"helm upgrade --install external-secrets · IRSA={eso_role_arn}")
    subprocess.run([
        "helm", "upgrade", "--install", "external-secrets", "external-secrets/external-secrets",
        "--namespace", "external-secrets", "--create-namespace",
        "--version", EXTERNAL_SECRETS_VERSION,
        "--set", "installCRDs=true",
        "--set-string", f"serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn={eso_role_arn}",
        "--wait", "--timeout", "5m",
    ], check=True)


# blackbox-exporter probe 대상 — Row 8 가용성 패널 외부 합성 모니터링.
#   dashboard / external-alb : http_2xx (200 기대)
#   auth-pod /auth/login     : http_2xx_30x (Entra OIDC redirect 302 정상)
BLACKBOX_PROBE_TARGETS_2XX = [
    "https://bookflow.myosoon.store",
    "http://bookflow-alb-external-1131217362.ap-northeast-1.elb.amazonaws.com/health",
]
BLACKBOX_PROBE_TARGETS_30X = [
    "https://bookflow.myosoon.store/auth/login",
]


def _helm_install_blackbox_exporter() -> None:
    """blackbox-exporter — 외부 엔드포인트 합성 모니터링 (Row 8 가용성 패널).
    prometheus-community/prometheus-blackbox-exporter 차트, namespace monitoring.
    Prometheus 의 blackbox scrape job 이 이 exporter 의 /probe 를 경유해
    probe_success / probe_http_status_code / probe_duration_seconds /
    probe_ssl_earliest_cert_expiry 메트릭을 수집.

    module 2종:
      http_2xx     — 차트 기본 (200 기대) · dashboard / external-alb
      http_2xx_30x — 200·30x 모두 허용 (auth-pod /auth/login OIDC redirect 302)
    """
    import tempfile, yaml as _yaml
    values_dict = {
        "config": {
            "modules": {
                "http_2xx": {
                    "prober": "http",
                    "timeout": "10s",
                    "http": {"valid_http_versions": ["HTTP/1.1", "HTTP/2.0"]},
                },
                "http_2xx_30x": {
                    "prober": "http",
                    "timeout": "10s",
                    "http": {
                        "valid_http_versions": ["HTTP/1.1", "HTTP/2.0"],
                        "valid_status_codes": [200, 301, 302, 303, 307, 308],
                    },
                },
            },
        },
    }
    values = _yaml.safe_dump(values_dict, sort_keys=False, allow_unicode=True)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(values)
        values_path = f.name
    try:
        log.info("helm upgrade --install prometheus-blackbox-exporter")
        subprocess.run([
            "helm", "upgrade", "--install", "prometheus-blackbox-exporter",
            "prometheus-community/prometheus-blackbox-exporter",
            "--namespace", "monitoring", "--create-namespace",
            "--version", BLACKBOX_EXPORTER_VERSION,
            "--values", values_path,
            "--wait", "--timeout", "5m",
        ], check=True)
    finally:
        os.unlink(values_path)


def _blackbox_scrape_config() -> str:
    """Prometheus extraScrapeConfigs 문자열 — blackbox job.
    표준 relabel: __address__ 를 타깃 URL 로 치환 → __param_target,
    실제 scrape 주소는 blackbox-exporter service 로 변경.
    auth-pod 는 302(OIDC redirect) 가 정상이라 별도 job(blackbox-30x)으로 분리.
    """
    targets_2xx = "\n".join(f"      - {t}" for t in BLACKBOX_PROBE_TARGETS_2XX)
    targets_30x = "\n".join(f"      - {t}" for t in BLACKBOX_PROBE_TARGETS_30X)
    bb_svc = "prometheus-blackbox-exporter.monitoring.svc.cluster.local:9115"
    return f"""- job_name: blackbox
  metrics_path: /probe
  params:
    module: [http_2xx]
  static_configs:
  - targets:
{targets_2xx}
  relabel_configs:
  - source_labels: [__address__]
    target_label: __param_target
  - source_labels: [__param_target]
    target_label: instance
  - target_label: __address__
    replacement: {bb_svc}
- job_name: blackbox-30x
  metrics_path: /probe
  params:
    module: [http_2xx_30x]
  static_configs:
  - targets:
{targets_30x}
  relabel_configs:
  - source_labels: [__address__]
    target_label: __param_target
  - source_labels: [__param_target]
    target_label: instance
  - target_label: __address__
    replacement: {bb_svc}
"""


def _helm_install_prometheus() -> None:
    """Prometheus server only — 엔지니어 운영 대시보드 Phase ② (B2).
    alertmanager / pushgateway / node-exporter / kube-state-metrics 는 비활성 (최소 구성).
    차트 기본 prometheus.yml 의 kubernetes-pods scrape job 은 그대로 둠 —
    BookFlow 8 Pod 가 prometheus.io/scrape annotation 으로 수집됨.
    extraScrapeConfigs 로 blackbox-exporter 경유 외부 엔드포인트 probe job 추가.
    """
    log.info("helm upgrade --install prometheus (server only)")
    subprocess.run([
        "helm", "upgrade", "--install", "prometheus", "prometheus-community/prometheus",
        "--namespace", "monitoring", "--create-namespace",
        "--version", PROMETHEUS_VERSION,
        "--set", "alertmanager.enabled=false",
        "--set", "prometheus-pushgateway.enabled=false",
        "--set", "prometheus-node-exporter.enabled=false",
        "--set", "kube-state-metrics.enabled=false",
        "--set-string", "extraScrapeConfigs=" + _blackbox_scrape_config(),
        "--wait", "--timeout", "5m",
    ], check=True)


def _ensure_grafana_admin_password() -> str:
    """Grafana admin password — Secrets Manager `bookflow/grafana/admin` 조회 후 없으면 생성.
    bookflow/rds/master-password 패턴 참고. 매 redeploy 시 동일 값 (idempotent).
    """
    import boto3, json, secrets
    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
    try:
        val = json.loads(sm.get_secret_value(SecretId="bookflow/grafana/admin")["SecretString"])
        log.info("  grafana admin password · 기존 Secrets Manager 값 사용")
        return val["password"]
    except sm.exceptions.ResourceNotFoundException:
        pw = secrets.token_urlsafe(16)
        sm.create_secret(
            Name="bookflow/grafana/admin",
            SecretString=json.dumps({"username": "admin", "password": pw}),
        )
        log.info("  grafana admin password · bookflow/grafana/admin Secret 신규 생성")
        return pw


def _is_placeholder_cred(cred: dict, key: str) -> bool:
    """Tier 00 CFN 이 만든 placeholder secret 판별 — 값 미투입 시 datasource skip 용.
    key 가 없거나 빈 문자열이거나 'PLACEHOLDER' 면 True."""
    val = cred.get(key)
    return not val or val == "PLACEHOLDER"


def _helm_install_grafana() -> None:
    """Grafana — 엔지니어 운영 대시보드 Phase ② (B3) + Phase ③ 트랙 4 멀티클라우드 datasource.
    Prometheus (default) + CloudWatch (IRSA) + Azure Monitor + GCP Monitoring datasource provisioning.
    ingress-nginx /grafana sub-path 서빙. admin password 는 Secrets Manager
    bookflow/grafana/admin (idempotent). 대시보드 패널은 다음 Phase ④.
    """
    import boto3, json, yaml as _yaml
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    admin_pw = _ensure_grafana_admin_password()

    # datasource 목록 — Prometheus 는 항상 · CloudWatch/Azure 는 자격증명 가용 시
    # uid 는 고정값 — 코드 정의 대시보드(infra/observability/dashboards) JSON 이
    # 이 고정 UID 를 참조하므로 datasource provisioning 에 명시 부여한다.
    datasources = [
        {
            "name": "Prometheus",
            "uid": "prometheus",
            "type": "prometheus",
            "access": "proxy",
            "url": "http://prometheus-server.monitoring.svc.cluster.local",
            "isDefault": True,
        },
    ]

    # CloudWatch datasource — authType default → Grafana Pod 의 IRSA 사용.
    # IRSA role ARN 은 CFN export bookflow-grafana-cloudwatch-role-arn (cert-manager 패턴).
    cf = boto3.client("cloudformation", region_name=region)
    cw_role_arn = next(
        (e["Value"] for e in cf.list_exports()["Exports"] if e["Name"] == "bookflow-grafana-cloudwatch-role-arn"),
        None,
    )
    datasources.append({
        "name": "CloudWatch",
        "uid": "cloudwatch",
        "type": "cloudwatch",
        "access": "proxy",
        "jsonData": {"authType": "default", "defaultRegion": "ap-northeast-1"},
    })
    if cw_role_arn:
        log.info(f"  grafana CloudWatch datasource · IRSA={cw_role_arn}")
    else:
        log.warn("bookflow-grafana-cloudwatch-role-arn export missing · IRSA annotation 없이 진행 (CloudWatch 인증 미동작 가능)")

    # Azure Monitor datasource — 자격증명은 Secrets Manager bookflow/azure/grafana-monitor.
    # grafana-azure-monitor-datasource 는 Grafana core 번들 → 별도 plugin install 불필요.
    sm = boto3.client("secretsmanager", region_name=region)
    try:
        azure_cred = json.loads(sm.get_secret_value(SecretId="bookflow/azure/grafana-monitor")["SecretString"])
        if _is_placeholder_cred(azure_cred, "clientSecret"):
            log.warn("bookflow/azure/grafana-monitor secret 값 미투입 (placeholder) · Azure Monitor datasource skip")
        else:
            datasources.append({
                "name": "Azure Monitor",
                "uid": "azure-monitor",
                "type": "grafana-azure-monitor-datasource",
                "access": "proxy",
                "jsonData": {
                    "azureAuthType": "clientsecret",
                    "cloudName": "azuremonitor",
                    "tenantId": azure_cred["tenantId"],
                    "clientId": azure_cred["clientId"],
                    "subscriptionId": azure_cred["subscriptionId"],
                },
                "secureJsonData": {"clientSecret": azure_cred["clientSecret"]},
            })
            log.info("  grafana Azure Monitor datasource · bookflow/azure/grafana-monitor 자격증명 사용")
    except sm.exceptions.ResourceNotFoundException:
        log.warn("bookflow/azure/grafana-monitor secret missing · Azure Monitor datasource skip")

    # GCP Cloud Monitoring datasource — 자격증명은 Secrets Manager bookflow/gcp/grafana-monitor
    # (GCP SA key JSON 전체). stackdriver datasource 는 Grafana core 번들 → plugin install 불필요.
    try:
        gcp_cred = json.loads(sm.get_secret_value(SecretId="bookflow/gcp/grafana-monitor")["SecretString"])
        if _is_placeholder_cred(gcp_cred, "private_key"):
            log.warn("bookflow/gcp/grafana-monitor secret 값 미투입 (placeholder) · GCP Monitoring datasource skip")
        else:
            datasources.append({
                "name": "GCP Monitoring",
                "uid": "gcp-monitoring",
                "type": "stackdriver",
                "access": "proxy",
                "jsonData": {
                    "authenticationType": "jwt",
                    "defaultProject": gcp_cred.get("project_id", "project-8ab6bf05-54d2-4f5d-b8d"),
                    "clientEmail": gcp_cred["client_email"],
                    "tokenUri": gcp_cred["token_uri"],
                },
                "secureJsonData": {"privateKey": gcp_cred["private_key"]},
            })
            log.info("  grafana GCP Monitoring datasource · bookflow/gcp/grafana-monitor 자격증명 사용")
    except sm.exceptions.ResourceNotFoundException:
        log.warn("bookflow/gcp/grafana-monitor secret missing · GCP Monitoring datasource skip")

    values_dict = {
        "adminUser": "admin",
        "adminPassword": admin_pw,
        "datasources": {
            "datasources.yaml": {"apiVersion": 1, "datasources": datasources},
        },
        # 대시보드 sidecar — monitoring ns 의 라벨된 configmap 을 자동 로드.
        # _apply_grafana_dashboards() 가 grafana_dashboard=1 라벨 configmap 을 만든다.
        # folderAnnotation: configmap 의 grafana_folder annotation 값을 Grafana
        # 폴더명으로 사용 → 9개 운영 대시보드를 "BookFlow 운영" 폴더로 묶는다.
        "sidecar": {
            "dashboards": {
                "enabled": True,
                "label": "grafana_dashboard",
                "labelValue": "1",
                "folder": "/tmp/dashboards",
                "folderAnnotation": "grafana_folder",
                "provider": {"foldersFromFilesStructure": True},
                "searchNamespace": "monitoring",
            },
        },
        "grafana.ini": {
            "server": {
                "root_url": "%(protocol)s://%(domain)s/grafana/",
                "serve_from_sub_path": True,
            },
            # auth — Grafana "Sign out" 클릭 시 redirect URL.
            # auth.proxy + forward-auth 구성에선 Grafana 자체 signout 만으론 부족하다 —
            # bookflow_session 쿠키가 그대로면 다음 요청에서 forward-auth 가 X-WEBAUTH-USER
            # 를 다시 주입해 자동 재로그인 루프가 된다. signout_redirect_url 을 auth-pod 의
            # /auth/logout 으로 보내 bookflow_session 쿠키 삭제 + Entra end_session 까지 진행.
            "auth": {
                "signout_redirect_url": "/auth/logout",
            },
            # auth.proxy — engineer 통합 로그인 (Phase ⑤).
            # ingress forward-auth 가 BookFlow 인증 통과한 engineer 요청에만
            # X-WEBAUTH-USER 헤더를 주입 → Grafana 가 그 헤더로 자동 로그인.
            # Grafana 자체 로그인 화면은 안 뜸. whitelist 는 in-cluster CIDR
            # (ingress-nginx Pod → grafana svc) 만 허용 — 외부 직접 위조 차단.
            "auth.proxy": {
                "enabled": True,
                "header_name": "X-WEBAUTH-USER",
                "header_property": "username",
                "auto_sign_up": True,
                "whitelist": "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
                # true 필수 — Grafana 가 X-WEBAUTH-USER 검증 후 세션 로그인 토큰을 발급.
                # false 면 토큰이 없는데도 Grafana SPA 가 auth-tokens/rotate · live/ws 를
                # 호출 → 401 "user token not found" → unauthorized + 새로고침 루프.
                "enable_login_token": True,
            },
            "users": {
                "auto_assign_org_role": "Editor",
            },
        },
        "ingress": {
            "enabled": True,
            "ingressClassName": "nginx",
            "hosts": ["bookflow.myosoon.store"],
            "path": "/grafana",
            # forward-auth: 모든 /grafana 요청을 dashboard-svc 가 검증.
            # role==engineer 면 200 + X-WEBAUTH-USER 응답헤더 → auth-response-headers
            # 로 upstream(Grafana) 에 전달. 아니면 401.
            # configuration-snippet(more_clear_input_headers) 제거: 클러스터 보안정책
            # allow-snippet-annotations=false 로 비활성 · auth-response-headers 의
            # proxy_set_header 가 위조 X-WEBAUTH-USER 를 auth 응답값으로 override → 중복 방어.
            "annotations": {
                "nginx.ingress.kubernetes.io/auth-url":
                    "http://dashboard-svc.bookflow.svc.cluster.local/internal/grafana-auth",
                "nginx.ingress.kubernetes.io/auth-response-headers": "X-WEBAUTH-USER",
            },
        },
    }
    # CloudWatch IRSA — Grafana SA 에 role-arn annotation (export 가용 시에만)
    if cw_role_arn:
        values_dict["serviceAccount"] = {
            "annotations": {"eks.amazonaws.com/role-arn": cw_role_arn},
        }
    values = _yaml.safe_dump(values_dict, sort_keys=False, allow_unicode=True)

    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(values)
        values_path = f.name
    try:
        log.info("helm upgrade --install grafana")
        subprocess.run([
            "helm", "upgrade", "--install", "grafana", "grafana/grafana",
            "--namespace", "monitoring", "--create-namespace",
            "--version", GRAFANA_VERSION,
            "--values", values_path,
            "--wait", "--timeout", "5m",
        ], check=True)
    finally:
        os.unlink(values_path)


def _apply_grafana_dashboards() -> None:
    """BookFlow 운영 대시보드 9개를 monitoring ns 의 configmap 으로 배포.

    infra/observability/dashboards/generated/*.json (레포 tracked · 빌드 산출물
    commit 본) 을 configmap 으로 만든다. grafana_dashboard=1 라벨이 붙어 있어
    Grafana sidecar 가 자동 로드한다. 배포 경로는 Foundation SDK 빌드에 의존하지
    않는다 — generated/ JSON 만 사용."""
    import json as _json

    platform_root = Path(__file__).resolve().parents[3]
    gen_dir = platform_root / "infra" / "observability" / "dashboards" / "generated"
    json_files = sorted(gen_dir.glob("row*.json"))
    if not json_files:
        log.warn(f"운영 대시보드 JSON 없음 ({gen_dir}) · configmap skip")
        return

    # 계정 의존 placeholder 치환 — row5 의 S3 버킷 (bookflow-mart-__AWS_ACCOUNT__) 등이
    # 배포 계정(admin 994.. / deploy 354..) 의 실재 버킷을 가리키도록 현재 STS 계정으로 치환.
    account = boto3.client("sts").get_caller_identity()["Account"]

    def _render(path):
        return path.read_text(encoding="utf-8").replace("__AWS_ACCOUNT__", account)

    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "bookflow-ops-dashboards",
            "namespace": "monitoring",
            "labels": {"grafana_dashboard": "1", "app.kubernetes.io/part-of": "bookflow"},
            # grafana_folder — sidecar folderAnnotation 이 이 값을 Grafana 폴더명으로
            # 사용 → 9개 운영 대시보드가 "BookFlow 운영" 폴더 하나로 묶인다.
            "annotations": {"grafana_folder": "BookFlow 운영"},
        },
        "data": {f.name: _render(f) for f in json_files},
    }
    log.info(f"운영 대시보드 configmap bookflow-ops-dashboards · {len(json_files)}개 대시보드")

    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(_json.dumps(cm))
        cm_path = f.name
    try:
        subprocess.run(["kubectl", "apply", "-f", cm_path], check=True)
    finally:
        os.unlink(cm_path)


ROUTE53_HOSTED_ZONE_ID = "Z0061717U502ZCK2HCCF"  # myosoon.store (deploy 계정 소유 영구 zone)
ROUTE53_ZONE_ACCOUNT = "354493396671"            # myosoon.store zone 소유 계정 (deploy)
ROUTE53_XACCT_ROLE = "arn:aws:iam::354493396671:role/bookflow-route53-xacct"
NLB_HOSTED_ZONE_ID = "Z31USIVHYNEOWT"  # NLB ap-northeast-1
PUBLIC_FQDN = "bookflow.myosoon.store"


def _route53_client():
    """myosoon.store zone 은 deploy 계정 소유. 현재 계정(admin)이 다르면 cross-account assume.
    deploy 계정에서 돌 땐 자기 zone → assume 불필요."""
    import boto3
    sts = boto3.client("sts")
    if sts.get_caller_identity()["Account"] == ROUTE53_ZONE_ACCOUNT:
        return boto3.client("route53")
    cr = sts.assume_role(RoleArn=ROUTE53_XACCT_ROLE,
                         RoleSessionName="bookflow-eks-addons-route53")["Credentials"]
    return boto3.client("route53",
                        aws_access_key_id=cr["AccessKeyId"],
                        aws_secret_access_key=cr["SecretAccessKey"],
                        aws_session_token=cr["SessionToken"])


def _update_route53_a_alias() -> None:
    """매일 새 NLB DNS → Route53 A alias 자동 UPSERT (bookflow.myosoon.store)."""
    import boto3
    r = subprocess.run(
        ["kubectl", "get", "svc", "-n", "ingress-nginx", "ingress-nginx-controller",
         "-o", "jsonpath={.status.loadBalancer.ingress[0].hostname}"],
        capture_output=True, text=True, check=True,
    )
    nlb_dns = r.stdout.strip()
    if not nlb_dns:
        log.err("NLB DNS not found · ingress-nginx LoadBalancer 미배포")
        raise SystemExit(1)
    log.info(f"Route53 UPSERT {PUBLIC_FQDN} → {nlb_dns}")
    _route53_client().change_resource_record_sets(
        HostedZoneId=ROUTE53_HOSTED_ZONE_ID,
        ChangeBatch={"Changes": [{
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": f"{PUBLIC_FQDN}.",
                "Type": "A",
                "AliasTarget": {
                    "HostedZoneId": NLB_HOSTED_ZONE_ID,
                    "DNSName": nlb_dns + ".",
                    "EvaluateTargetHealth": False,
                },
            },
        }]},
    )
    log.info(f"  ✓ {PUBLIC_FQDN} alias updated")


def _apply_cluster_secret_store() -> None:
    """ClusterSecretStore `bookflow-aws-secrets` — 모든 Pod 의 ExternalSecret 가 reference."""
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    yaml = f"""
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: bookflow-aws-secrets
spec:
  provider:
    aws:
      service: SecretsManager
      region: {region}
      auth:
        jwt:
          serviceAccountRef:
            name: external-secrets
            namespace: external-secrets
""".lstrip()
    subprocess.run(["kubectl", "apply", "-f", "-"], input=yaml, text=True, check=True)
    log.info("  ✓ ClusterSecretStore bookflow-aws-secrets")


def _apply_storage_class() -> None:
    """default StorageClass `gp3` (EBS CSI) — k8s 1.33 에서 in-tree provisioner(kubernetes.io/aws-ebs)
    가 제거돼 기본 gp2 SC 가 죽음. ebs.csi.aws.com 애드온을 쓰는 gp3 SC 를 새 default 로 지정.
    WaitForFirstConsumer 로 Pod scheduling 후 zone 일치하는 EBS 생성. Prometheus PVC bind 전제.
    """
    yaml = """
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: ebs.csi.aws.com
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
parameters:
  type: gp3
""".lstrip()
    subprocess.run(["kubectl", "apply", "-f", "-"], input=yaml, text=True, check=True)
    log.info("  ✓ StorageClass gp3 (default · ebs.csi.aws.com)")
    # 기존 in-tree gp2 SC 의 default annotation 제거 — 둘 다 default 면 충돌. gp2 없으면 무시.
    subprocess.run(
        ["kubectl", "patch", "storageclass", "gp2", "-p",
         '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}'],
        check=False,
    )
    log.info("  ✓ StorageClass gp2 default annotation 제거 (있을 경우)")


def _helm_install_webhook_duckdns(token: str) -> None:
    log.info("helm upgrade --install cert-manager-webhook-duckdns")
    subprocess.run([
        "helm", "upgrade", "--install", "cert-manager-webhook-duckdns",
        "mmontes11/cert-manager-webhook-duckdns",
        "--namespace", "cert-manager",
        "--version", WEBHOOK_DUCKDNS_VERSION,
        "--set", f"duckdns.token={token}",
        "--set", "logLevel=2",
        "--wait", "--timeout", "3m",
    ], check=True)


def _ensure_duckdns_token_secret(token: str) -> None:
    log.info("ensure Secret duckdns-token in ingress-nginx namespace")
    subprocess.run(
        ["kubectl", "create", "namespace", "ingress-nginx", "--dry-run=client", "-o", "yaml"],
        check=True, capture_output=True,
    )
    # idempotent secret
    yaml = subprocess.run(
        ["kubectl", "create", "secret", "generic", "duckdns-token",
         "-n", "ingress-nginx", f"--from-literal=token={token}",
         "--dry-run=client", "-o", "yaml"],
        check=True, capture_output=True, text=True,
    ).stdout
    subprocess.run(["kubectl", "apply", "-f", "-"], input=yaml, text=True, check=True)


def _alter_rds_pod_roles(rds_pw: str) -> None:
    """RDS 의 6 pod role password 를 master password 와 일치시킴 (idempotent).

    K8s Secret 의 AUTH_RDS_PASSWORD 가 master password 라 RDS role password 도 같아야 함.
    매일 destroy/redeploy 시 003_grants.sql 이 'CHANGE_ME_*' placeholder 로 reset → 여기서 정정.
    """
    import boto3, json, time, base64
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    ssm = boto3.client("ssm", region_name=region)
    ec2 = boto3.client("ec2", region_name=region)
    rds = boto3.client("rds", region_name=region)

    instances = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": ["bookflow-ansible-node"]},
        {"Name": "instance-state-name", "Values": ["running"]},
    ])["Reservations"]
    if not instances:
        log.warn("ansible-node not found · skip RDS role sync")
        return
    instance_id = instances[0]["Instances"][0]["InstanceId"]
    rds_host = rds.describe_db_instances(DBInstanceIdentifier="bookflow-postgres")["DBInstances"][0]["Endpoint"]["Address"]

    sql = " ".join([
        f"ALTER ROLE {r} WITH PASSWORD $bf$" + rds_pw + "$bf$;"
        for r in ["auth_pod", "dashboard_svc", "inventory_svc", "forecast_svc", "decision_svc", "intervention_svc", "notification_svc"]
    ])
    sql_b64 = base64.b64encode(sql.encode()).decode()
    cmd = (
        f"echo {sql_b64} | base64 -d > /tmp/_alter_roles.sql && "
        f"PGPASSWORD={json.dumps(rds_pw)} psql -h {rds_host} -U bookflow_admin -d bookflow "
        f"-v ON_ERROR_STOP=1 -f /tmp/_alter_roles.sql"
    )
    # retry — start-day 흐름상 seed.sh (003_grants.sql) 가 eks-addons 보다 늦을 수 있음.
    # role 미존재 시 30s × 6회 = 최대 3분 대기 (seed 완료 후 자동 정합).
    last_err = ""
    for attempt in range(6):
        r = ssm.send_command(InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                             Parameters={"commands": [cmd]}, Comment=f"sync pod role passwords (try {attempt+1})")
        cid = r["Command"]["CommandId"]
        for _ in range(20):
            time.sleep(3)
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=instance_id)
            if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
                break
        if inv["Status"] == "Success":
            log.info(f"  ✓ RDS pod role passwords synced (try {attempt+1})")
            return
        last_err = inv.get("StandardErrorContent", "") + inv.get("StandardOutputContent", "")
        # role 미존재 = seed 아직 안 됨 → wait. 다른 에러면 즉시 fail.
        if 'does not exist' in last_err.lower() or 'role' in last_err.lower():
            log.warn(f"ALTER ROLE try {attempt+1}/6 · seed 대기 (role 미존재) · 30s sleep")
            time.sleep(30)
            continue
        break
    log.err(f"ALTER ROLE failed after retry: {last_err[:300]}")
    raise SystemExit(1)


def _sync_rds_pod_roles() -> None:
    """RDS pod role password 를 master password 와 일치 (003_grants.sql 의 CHANGE_ME_* 정정).
    K8s Secret sync 는 ESO 가 ExternalSecret 으로 처리 (eks-pods/*/k8s/externalsecret.yaml).
    """
    import boto3, json
    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
    rds_pw = json.loads(sm.get_secret_value(SecretId="bookflow/rds/master-password")["SecretString"])["password"]
    _alter_rds_pod_roles(rds_pw)


def _patch_clusterissuer_xacct_role() -> None:
    """admin: myosoon.store zone 은 deploy 계정 소유 → ClusterIssuer route53 solver 에
    cross-account role 주입. cert-manager 가 이 role 을 assume 해 DNS-01 TXT 를 작성.
    deploy 계정에선 자기 zone 직접 → 호출 안 함 (_apply_manifests 가 BOOKFLOW_ENV 로 분기)."""
    patch = ('[{"op":"add","path":"/spec/acme/solvers/0/dns01/route53/role",'
             f'"value":"{ROUTE53_XACCT_ROLE}"}}]')
    for ci in ("letsencrypt-prod", "letsencrypt-staging"):
        subprocess.run(["kubectl", "patch", "clusterissuer", ci, "--type=json", "-p", patch],
                       check=False)
    log.info(f"  ClusterIssuer route53 solver cross-account role 주입: {ROUTE53_XACCT_ROLE}")


def _apply_manifests() -> None:
    # bookflow namespace 가 certificate.yaml 보다 먼저 존재해야 함 (cert-manager Certificate 가 namespace=bookflow)
    subprocess.run(
        ["kubectl", "create", "namespace", "bookflow", "--dry-run=client", "-o", "yaml"],
        check=True, capture_output=True, text=True,
    )
    yaml = subprocess.run(
        ["kubectl", "create", "namespace", "bookflow", "--dry-run=client", "-o", "yaml"],
        check=True, capture_output=True, text=True,
    ).stdout
    subprocess.run(["kubectl", "apply", "-f", "-"], input=yaml, text=True, check=True)
    log.info("  ✓ namespace bookflow")

    # envsubst 공통 변수 (admin path 전용 · cicd-eks 의 buildspec envsubst 와 무관)
    # subs 에 없는 ${...} (예: cronjob 의 ${IP}, ${TOKEN}) 는 그대로 둠 → shell expansion
    import boto3 as _b
    _region = os.environ.get("AWS_REGION", "ap-northeast-1")
    _account = _b.client("sts", region_name=_region).get_caller_identity()["Account"]
    # RDS_HOST 동적 fetch (admin 매일 destroy/redeploy 시 endpoint 변동 가능 · stale 방지)
    try:
        _rds_host = _b.client("rds", region_name=_region).describe_db_instances(
            DBInstanceIdentifier="bookflow-postgres"
        )["DBInstances"][0]["Endpoint"]["Address"]
    except Exception as e:
        log.warn(f"RDS host fetch failed (configmap RDS_HOST 빈 채로 apply): {e}")
        _rds_host = ""
    common_subs = {
        "ECR_REGISTRY": f"{_account}.dkr.ecr.{_region}.amazonaws.com",
        "IMAGE_TAG": os.environ.get("BOOKFLOW_IMAGE_TAG", "latest"),
        "PROJECT_NAME": "bookflow",
        "DOMAIN": os.environ.get("BOOKFLOW_DUCKDNS_FQDN", "bookflow.duckdns.org"),
        "RDS_HOST": _rds_host,
        "REDIS_HOST": os.environ.get("REDIS_HOST", ""),
        "GCP_PROJECT_ID": os.environ.get("GCP_PROJECT_ID", "project-8ab6bf05-54d2-4f5d-b8d"),
        "GCP_VERTEX_INVOKE_URL": os.environ.get(
            "GCP_VERTEX_INVOKE_URL",
            "https://asia-northeast1-project-8ab6bf05-54d2-4f5d-b8d.cloudfunctions.net/bookflow-vertex-invoke",
        ),
    }

    def _apply_with_subs(manifest: Path, extra: dict[str, str] | None = None) -> None:
        text = manifest.read_text(encoding="utf-8")
        merged = {**common_subs, **(extra or {})}
        for k, v in merged.items():
            text = text.replace("${" + k + "}", v)
        log.info(f"kubectl apply -f {manifest.parent.name}/{manifest.name}")
        # Windows stdin cp949 회피 위해 bytes 로 전달 (UTF-8 encoded)
        subprocess.run(["kubectl", "apply", "-f", "-"], input=text.encode("utf-8"), check=True)

    apps = _apps_dir()
    # 인프라 manifests (먼저 · cluster-issuer Route53 + certificate + dashboard ingress)
    # Route53 DNS-01 로 전환 (2026-05-14) — duckdns-sync 제거.
    infra_manifests = [
        apps / "eks-pods" / "auth-pod" / "k8s" / "cluster-issuer.yaml",
        apps / "eks-pods" / "auth-pod" / "k8s" / "certificate.yaml",
        apps / "eks-pods" / "dashboard-svc" / "k8s" / "ingress.yaml",
    ]
    for m in infra_manifests:
        if not m.exists():
            log.warn(f"manifest missing (skip): {m}")
            continue
        _apply_with_subs(m)
        # cluster-issuer apply 직후 — admin 은 myosoon.store(deploy zone) cross-account
        # role 을 solver 에 주입 (certificate.yaml apply 보다 먼저 → challenge 가 role config 사용).
        if m.name == "cluster-issuer.yaml" and os.environ.get("BOOKFLOW_ENV", "deploy") == "admin":
            _patch_clusterissuer_xacct_role()

    # 7 pod manifests (admin 환경 전용 · deploy 는 cicd-eks 가 build/push/apply 자동 처리)
    # BOOKFLOW_ENV=admin 일 때만 실행 — deploy 에서 중복 apply 회피
    if os.environ.get("BOOKFLOW_ENV", "deploy") == "admin":
        log.info("admin 환경 · 7 pod manifest auto-apply (cicd 없음)")
        pod_dirs = ["auth-pod", "dashboard-svc", "decision-svc", "forecast-svc",
                    "intervention-svc", "inventory-svc", "notification-svc"]
        pod_manifest_names = {"deployment.yaml", "service.yaml", "configmap.yaml",
                              "externalsecret.yaml", "serviceaccount.yaml", "hpa.yaml",
                              "cronjob.yaml"}
        for pod in pod_dirs:
            k8s_dir = apps / "eks-pods" / pod / "k8s"
            if not k8s_dir.exists():
                log.warn(f"pod k8s dir missing (skip): {pod}")
                continue
            for m in sorted(k8s_dir.glob("*.yaml")):
                if m.name not in pod_manifest_names:
                    continue
                if pod == "auth-pod" and m.name in {"cluster-issuer.yaml", "certificate.yaml"}:
                    continue
                _apply_with_subs(m, extra={"POD_NAME": pod})

        # publisher-watcher CronJob (별도 위치)
        pw_cronjob = apps / "eks-pods" / "publisher-watcher" / "k8s" / "cronjob.yaml"
        if pw_cronjob.exists():
            _apply_with_subs(pw_cronjob, extra={"POD_NAME": "publisher-watcher"})
    else:
        log.info("deploy 환경 · 7 pod manifest 는 cicd-eks 가 처리 · skip")


def deploy() -> None:
    log.step("=== task-eks-addons · helm install + manifests apply ===")
    _ensure_tools()
    _ensure_kubeconfig()

    _helm_repo_add()
    _helm_install_ingress_nginx()
    _helm_install_cert_manager()
    _helm_install_external_secrets()
    _apply_cluster_secret_store()
    _apply_storage_class()
    _helm_install_blackbox_exporter()
    _helm_install_prometheus()
    _helm_install_grafana()
    _apply_grafana_dashboards()
    _update_route53_a_alias()
    _apply_manifests()
    # BOOKFLOW_SKIP_RDS_SYNC=1 이면 ALTER ROLE + rollout restart 건너뜀
    # eks.sh 병렬 실행 시 seed.sh 미완료로 role 없어 실패하는 문제 방지
    # start-day.sh step 5/5 에서 플래그 없이 재호출 → full sync
    if os.environ.get("BOOKFLOW_SKIP_RDS_SYNC") == "1":
        log.info("BOOKFLOW_SKIP_RDS_SYNC=1 · ALTER ROLE + rollout restart skip (start-day step 5/5 에서 처리)")
        return
    _sync_rds_pod_roles()
    # ALTER ROLE 후 7 pod 의 connection pool 캐시 무효화 — rollout restart 로 새 password 적용
    log.info("rollout restart 7 pods · ALTER ROLE 적용 위해 connection pool 재생성")
    subprocess.run(
        ["kubectl", "rollout", "restart", "deployment",
         "auth-pod", "dashboard-svc", "decision-svc", "forecast-svc",
         "intervention-svc", "inventory-svc", "notification-svc",
         "-n", "bookflow"],
        check=False,
    )

    log.step("=== task-eks-addons done ===")
    log.info("https://bookflow.myosoon.store/ should serve in 60-90s after first cert issuance")


def destroy() -> None:
    log.step("=== task-eks-addons-down ===")
    _ensure_tools()
    _ensure_kubeconfig()

    # Reverse order
    for cmd in [
        ["helm", "uninstall", "grafana", "-n", "monitoring", "--ignore-not-found"],
        ["helm", "uninstall", "prometheus", "-n", "monitoring", "--ignore-not-found"],
        ["helm", "uninstall", "prometheus-blackbox-exporter", "-n", "monitoring", "--ignore-not-found"],
        ["helm", "uninstall", "cert-manager-webhook-duckdns", "-n", "cert-manager", "--ignore-not-found"],
        ["helm", "uninstall", "cert-manager", "-n", "cert-manager", "--ignore-not-found"],
        ["helm", "uninstall", "ingress-nginx", "-n", "ingress-nginx", "--ignore-not-found"],
        ["kubectl", "delete", "namespace", "monitoring", "--ignore-not-found=true"],
        ["kubectl", "delete", "namespace", "cert-manager", "--ignore-not-found=true"],
        ["kubectl", "delete", "namespace", "ingress-nginx", "--ignore-not-found=true"],
    ]:
        subprocess.run(cmd, check=False)
    log.step("=== task-eks-addons-down done ===")
