"""task-client-vpn · Client VPN Endpoint ( OpenVPN access)."""
from ..lib import Stack, log


def deploy() -> None:
    log.step("=== task-client-vpn ===")
    if not Stack(tier="10", name="vpc-bookflow-ai", template="").exists():
        log.err("vpc-bookflow-ai "); raise SystemExit(1)

    hz_id = Stack(tier="10", name="route53", template="").outputs().get("HostedZoneId")
    params = {}
    if hz_id:
        params["Route53HostedZoneId"] = hz_id

    Stack(tier="60", name="client-vpn",
          template="60-network-cross-cloud/client-vpn.yaml",
          parameters=params).deploy()

    log.step("=== task-client-vpn  ===")
    log.info("OVPN config: aws ec2 export-client-vpn-client-configuration --client-vpn-endpoint-id <id> --output text > bookflow-client.ovpn")


def destroy() -> None:
    log.step("=== task-client-vpn-down ===")
    Stack(tier="60", name="client-vpn", template="").destroy()
    log.step("=== task-client-vpn-down  ===")
