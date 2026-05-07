"""task-auth-pod · NAT + Azure VPN + endpoints (Auth Pod )."""
import os

from ..lib import Stack, log


def deploy() -> None:
    log.step("=== task-auth-pod · NAT + Azure VPN ===")
    if not Stack(tier="10", name="vpc-egress", template="").exists():
        log.err("vpc-egress "); raise SystemExit(1)
    if not Stack(tier="30", name="eks-cluster", template="").exists():
        log.warn("eks-cluster  · Pod   task-msa-pods ")

    if not Stack(tier="10", name="endpoints-bookflow-ai", template="").exists():
        Stack(tier="10", name="endpoints-bookflow-ai",
              template="10-network-core/endpoints/endpoints-bookflow-ai.yaml").deploy()

    Stack(tier="50", name="nat-gateway",
          template="50-network-traffic/nat-gateway.yaml").deploy()
    Stack(tier="60", name="tgw",
          template="60-network-cross-cloud/tgw.yaml").deploy()

    azure_ip = os.environ.get("BOOKFLOW_AZURE_VPN_GW_IP", "").strip()
    azure_psk = os.environ.get("BOOKFLOW_AZURE_VPN_PSK", "").strip()
    if azure_ip and azure_ip != "0.0.0.0":
        Stack(tier="10", name="customer-gateway",
              template="10-network-core/customer-gateway.yaml",
              parameters={"AzureVpnGatewayIp": azure_ip}).deploy()
        params = {"EnableAzureVpn": "true"}
        if azure_psk:
            params["AzurePresharedKey"] = azure_psk
        Stack(tier="60", name="vpn-site-to-site",
              template="60-network-cross-cloud/vpn-site-to-site.yaml",
              parameters=params).deploy()
    else:
        log.warn("BOOKFLOW_AZURE_VPN_GW_IP   · Azure VPN skip")
        log.info('  $env:BOOKFLOW_AZURE_VPN_GW_IP = "  IP"')

    log.step("=== task-auth-pod  ===")


def destroy() -> None:
    log.step("=== task-auth-pod-down ===")
    log.warn("vpn-site-to-site stack  destroy (Azure + GCP )")
    Stack(tier="60", name="vpn-site-to-site", template="").destroy()
    if not Stack(tier="60", name="vpn-site-to-site", template="").exists():
        Stack(tier="60", name="tgw", template="").destroy()
    Stack(tier="50", name="nat-gateway", template="").destroy()
    log.info("endpoints-bookflow-ai  task-msa-pods   · ")
    log.step("=== task-auth-pod-down  ===")
