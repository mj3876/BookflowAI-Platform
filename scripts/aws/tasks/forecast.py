"""task-forecast · GCP HA VPN (forecast-svc Pod → Vertex AI Endpoint)."""
import os

from ..lib import Stack, log


def deploy() -> None:
    log.step("=== task-forecast · GCP HA VPN ===")
    if not Stack(tier="10", name="customer-gateway", template="").exists():
        log.err("customer-gateway  · base-up "); raise SystemExit(1)

    Stack(tier="60", name="tgw",
          template="60-network-cross-cloud/tgw.yaml").deploy()

    gcp_ip = os.environ.get("BOOKFLOW_GCP_VPN_GW_IP", "").strip()
    gcp_psk = os.environ.get("BOOKFLOW_GCP_VPN_PSK", "").strip()
    if gcp_ip and gcp_ip != "0.0.0.0":
        Stack(tier="10", name="customer-gateway",
              template="10-network-core/customer-gateway.yaml",
              parameters={"GcpHaVpnIp": gcp_ip}).deploy()
        params = {"EnableGcpVpn": "true"}
        if gcp_psk:
            params["GcpPresharedKey"] = gcp_psk
        Stack(tier="60", name="vpn-site-to-site",
              template="60-network-cross-cloud/vpn-site-to-site.yaml",
              parameters=params).deploy()
    else:
        log.warn("BOOKFLOW_GCP_VPN_GW_IP   · GCP VPN skip")
        log.info('  $env:BOOKFLOW_GCP_VPN_GW_IP = "  GCP HA VPN IP"')

    log.step("=== task-forecast  ===")


def destroy() -> None:
    log.step("=== task-forecast-down ===")
    Stack(tier="60", name="vpn-site-to-site", template="").destroy()
    log.info("TGW  task-auth-pod   ·  (  task-auth-pod-down  )")
    log.step("=== task-forecast-down  ===")
