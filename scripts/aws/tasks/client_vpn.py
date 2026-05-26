"""task-client-vpn · Client VPN Endpoint ( OpenVPN access)."""
import os
import time

import boto3

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


def _disassociate_all(endpoint_id: str) -> None:
    """CFN 외부에서 추가된 association 포함 모두 해제 후 대기."""
    ec2 = boto3.client("ec2", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
    resp = ec2.describe_client_vpn_target_networks(ClientVpnEndpointId=endpoint_id)
    networks = resp.get("ClientVpnTargetNetworks", [])

    for assoc in networks:
        status = assoc["Status"]["Code"]
        if status in ("associated", "associating"):
            assoc_id = assoc["AssociationId"]
            subnet_id = assoc.get("TargetNetworkId", "")
            log.info(f"  disassociate {assoc_id} (subnet={subnet_id})")
            ec2.disassociate_client_vpn_target_network(
                ClientVpnEndpointId=endpoint_id,
                AssociationId=assoc_id,
            )

    waited = 0
    while waited < 180:
        time.sleep(15)
        waited += 15
        resp = ec2.describe_client_vpn_target_networks(ClientVpnEndpointId=endpoint_id)
        pending = [
            a["AssociationId"]
            for a in resp.get("ClientVpnTargetNetworks", [])
            if a["Status"]["Code"] != "disassociated"
        ]
        if not pending:
            log.info(f"  Client VPN ENI 해제 완료 ({waited}s 경과)")
            return
        log.info(f"  [{waited}s] 해제 대기: {pending}")

    log.warn("  association 해제 타임아웃 (180s) — 스택 삭제 계속 시도")


def destroy() -> None:
    log.step("=== task-client-vpn-down ===")
    stack = Stack(tier="60", name="client-vpn", template="")
    if not stack.exists():
        log.info("bookflow-60-client-vpn 스택 없음 — skip")
        log.step("=== task-client-vpn-down  ===")
        return

    endpoint_id = stack.outputs().get("ClientVpnEndpointId")
    if endpoint_id:
        _disassociate_all(endpoint_id)

    stack.destroy()
    log.step("=== task-client-vpn-down  ===")
