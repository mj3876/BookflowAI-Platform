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
    log.info("helm upgrade --install cert-manager (with DNS-01 recursive nameservers)")
    # extraArgs: --dns01-recursive-nameservers-only + --dns01-recursive-nameservers=8.8.8.8:53,1.1.1.1:53
    # → public recursive resolver 사용 (EKS Worker Node 의 외부 :53 차단 우회)
    subprocess.run([
        "helm", "upgrade", "--install", "cert-manager", "jetstack/cert-manager",
        "--namespace", "cert-manager", "--create-namespace",
        "--version", CERT_MANAGER_VERSION,
        "--set", "crds.enabled=true",
        "--set", "extraArgs={--dns01-recursive-nameservers-only,--dns01-recursive-nameservers=8.8.8.8:53\\,1.1.1.1:53}",
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
    r = ssm.send_command(InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                         Parameters={"commands": [cmd]}, Comment="sync 6 pod role passwords")
    cid = r["Command"]["CommandId"]
    for _ in range(20):
        time.sleep(3)
        inv = ssm.get_command_invocation(CommandId=cid, InstanceId=instance_id)
        if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            break
    if inv["Status"] != "Success":
        log.err(f"ALTER ROLE failed: {inv.get('StandardErrorContent','')[:200]}")
        raise SystemExit(1)
    log.info("  ✓ RDS 6 pod role passwords synced with master")


def _sync_rds_pod_roles() -> None:
    """RDS pod role password 를 master password 와 일치 (003_grants.sql 의 CHANGE_ME_* 정정).
    K8s Secret sync 는 ESO 가 ExternalSecret 으로 처리 (eks-pods/*/k8s/externalsecret.yaml).
    """
    import boto3, json
    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
    rds_pw = json.loads(sm.get_secret_value(SecretId="bookflow/rds/master-password")["SecretString"])["password"]
    _alter_rds_pod_roles(rds_pw)


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
    # 인프라 manifests (먼저 · cert-manager + duckdns + dashboard ingress)
    infra_manifests = [
        apps / "eks-pods" / "auth-pod" / "k8s" / "cluster-issuer.yaml",
        apps / "eks-pods" / "auth-pod" / "k8s" / "certificate.yaml",
        apps / "eks-pods" / "duckdns-sync" / "k8s" / "cronjob.yaml",
        apps / "eks-pods" / "dashboard-svc" / "k8s" / "ingress.yaml",
    ]
    for m in infra_manifests:
        if not m.exists():
            log.warn(f"manifest missing (skip): {m}")
            continue
        _apply_with_subs(m)

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

    token = os.environ.get("BOOKFLOW_DUCKDNS_TOKEN")
    if not token:
        log.err("BOOKFLOW_DUCKDNS_TOKEN missing in scripts/aws/config/.env.local")
        raise SystemExit(1)

    _helm_repo_add()
    _helm_install_ingress_nginx()
    _helm_install_cert_manager()
    _helm_install_webhook_duckdns(token)
    _helm_install_external_secrets()
    _ensure_duckdns_token_secret(token)
    _apply_cluster_secret_store()
    _apply_manifests()
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
    log.info("https://bookflow.duckdns.org/ should serve in 60-90s after first cert issuance")


def destroy() -> None:
    log.step("=== task-eks-addons-down ===")
    _ensure_tools()
    _ensure_kubeconfig()

    # Reverse order
    for cmd in [
        ["helm", "uninstall", "cert-manager-webhook-duckdns", "-n", "cert-manager", "--ignore-not-found"],
        ["helm", "uninstall", "cert-manager", "-n", "cert-manager", "--ignore-not-found"],
        ["helm", "uninstall", "ingress-nginx", "-n", "ingress-nginx", "--ignore-not-found"],
        ["kubectl", "delete", "namespace", "cert-manager", "--ignore-not-found=true"],
        ["kubectl", "delete", "namespace", "ingress-nginx", "--ignore-not-found=true"],
    ]:
        subprocess.run(cmd, check=False)
    log.step("=== task-eks-addons-down done ===")
