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


def _sync_pod_secrets() -> None:
    """AWS Secrets Manager → K8s Secret 자동 sync + RDS role password 정합.

    매일 destroy/redeploy 시 cicd-eks 가 secret.example.yaml 무시 (buildspec glob fix) +
    이 함수가 idempotent 보장.

    - bookflow/auth/entra-client-secret → auth-pod-secret.AUTH_ENTRA_CLIENT_SECRET
    - bookflow/auth/jwt-signing-key     → 7 Pod 의 *-secret.AUTH_JWT_SIGNING_KEY
    - bookflow/rds/master-password      → 7 Pod 의 *-secret.{POD}_RDS_PASSWORD + auth-pod-secret.AUTH_RDS_PASSWORD
    - + RDS 의 6 pod role password 도 master 와 일치 (ALTER ROLE)
    """
    import boto3, json
    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))

    log.info("sync K8s Secrets from AWS Secrets Manager")
    entra = json.loads(sm.get_secret_value(SecretId="bookflow/auth/entra-client-secret")["SecretString"])
    jwt_key = sm.get_secret_value(SecretId="bookflow/auth/jwt-signing-key")["SecretString"]
    rds_pw = json.loads(sm.get_secret_value(SecretId="bookflow/rds/master-password")["SecretString"])["password"]

    # RDS pod role password sync (master 와 일치 · 003_grants.sql 의 CHANGE_ME_* 정정)
    _alter_rds_pod_roles(rds_pw)

    # auth-pod-secret (entra + jwt + rds)
    yaml = subprocess.run(
        ["kubectl", "create", "secret", "generic", "auth-pod-secret", "-n", "bookflow",
         f"--from-literal=AUTH_ENTRA_CLIENT_SECRET={entra['client_secret']}",
         f"--from-literal=AUTH_JWT_SIGNING_KEY={jwt_key}",
         f"--from-literal=AUTH_RDS_PASSWORD={rds_pw}",
         "--dry-run=client", "-o", "yaml"],
        check=True, capture_output=True, text=True,
    ).stdout
    subprocess.run(["kubectl", "apply", "-f", "-"], input=yaml, text=True, check=True)
    log.info("  ✓ auth-pod-secret")

    # 6 Pod dual-mode auth · *_RDS_PASSWORD + AUTH_JWT_SIGNING_KEY 둘 다 sync
    pod_to_envprefix = {
        "dashboard-svc": "DASHBOARD",
        "decision-svc": "DECISION",
        "forecast-svc": "FORECAST",
        "intervention-svc": "INTERVENTION",
        "inventory-svc": "INVENTORY",
        "notification-svc": "NOTIFICATION",
    }
    for pod, prefix in pod_to_envprefix.items():
        yaml = subprocess.run(
            ["kubectl", "create", "secret", "generic", f"{pod}-secret", "-n", "bookflow",
             f"--from-literal={prefix}_RDS_PASSWORD={rds_pw}",
             f"--from-literal=AUTH_JWT_SIGNING_KEY={jwt_key}",
             "--dry-run=client", "-o", "yaml"],
            check=True, capture_output=True, text=True,
        ).stdout
        subprocess.run(["kubectl", "apply", "-f", "-"], input=yaml, text=True, check=True)
    log.info("  ✓ 6 Pod RDS password + JWT key sync")


def _apply_manifests() -> None:
    apps = _apps_dir()
    manifests = [
        apps / "eks-pods" / "auth-pod" / "k8s" / "cluster-issuer.yaml",
        apps / "eks-pods" / "auth-pod" / "k8s" / "certificate.yaml",
        apps / "eks-pods" / "duckdns-sync" / "k8s" / "cronjob.yaml",
        apps / "eks-pods" / "dashboard-svc" / "k8s" / "ingress.yaml",
    ]
    for m in manifests:
        if not m.exists():
            log.warn(f"manifest missing (skip): {m}")
            continue
        log.info(f"kubectl apply -f {m.name}")
        subprocess.run(["kubectl", "apply", "-f", str(m)], check=True)


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
    _ensure_duckdns_token_secret(token)
    _apply_manifests()
    _sync_pod_secrets()

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
