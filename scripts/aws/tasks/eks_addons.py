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


def _sync_pod_secrets() -> None:
    """AWS Secrets Manager → K8s Secret 자동 sync (idempotent · 매일 redeploy 시 자동).

    - bookflow/auth/entra-client-secret  → auth-pod-secret.AUTH_ENTRA_CLIENT_SECRET
    - bookflow/auth/jwt-signing-key      → 7 Pod 의 *-secret.AUTH_JWT_SIGNING_KEY
    - bookflow/rds/master-password       → auth-pod-secret.AUTH_RDS_PASSWORD (auth-pod 한정)
    """
    import boto3, json
    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))

    log.info("sync K8s Secrets from AWS Secrets Manager")
    entra = json.loads(sm.get_secret_value(SecretId="bookflow/auth/entra-client-secret")["SecretString"])
    jwt_key = sm.get_secret_value(SecretId="bookflow/auth/jwt-signing-key")["SecretString"]
    rds_pw = json.loads(sm.get_secret_value(SecretId="bookflow/rds/master-password")["SecretString"])["password"]

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

    # 6 Pod (dual-mode auth · JWT key 만 같이 보장 · 기존 RDS_PASSWORD 는 patch)
    for p in ["dashboard-svc", "decision-svc", "forecast-svc", "intervention-svc", "inventory-svc", "notification-svc"]:
        # JWT key 만 patch (기존 secret 의 다른 key 는 보존)
        import base64
        jwt_b64 = base64.b64encode(jwt_key.encode()).decode()
        # 기존 secret 의 ${POD}_RDS_PASSWORD 는 manifest 에서 placeholder 로 들어감 (별도 sync TODO)
        subprocess.run(
            ["kubectl", "patch", "secret", f"{p}-secret", "-n", "bookflow",
             "--type=json",
             "-p", f'[{{"op":"replace","path":"/data/AUTH_JWT_SIGNING_KEY","value":"{jwt_b64}"}}]'],
            check=False,  # secret 없으면 silent skip · manifest apply 후 다음 run 에서 sync
            capture_output=True,
        )
    log.info("  ✓ 6 Pod JWT key sync")


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
