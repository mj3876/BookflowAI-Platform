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
