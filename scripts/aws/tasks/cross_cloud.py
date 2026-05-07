"""cross-cloud minimum deploy · Tier 10 VPC×4 + CGW + Tier 60 TGW + VPN.

cross-cloud (AWS ↔ Azure ↔ GCP)     deploy.
EKS · ECS · RDS  cross-cloud    skip.
"""
import os

from ..lib import Stack, log

VPCS = [
    ("vpc-bookflow-ai",  "10-network-core/vpc-bookflow-ai.yaml"),
    ("vpc-sales-data",   "10-network-core/vpc-sales-data.yaml"),
    ("vpc-egress",       "10-network-core/vpc-egress.yaml"),
    ("vpc-data",         "10-network-core/vpc-data.yaml"),
]


def deploy() -> None:
    log.step("=== cross-cloud minimum · 4 VPC + CGW + TGW + VPN ===")

    azure_ip = os.environ.get("BOOKFLOW_AZURE_VPN_GW_IP", "0.0.0.0")
    azure_psk = os.environ.get("BOOKFLOW_AZURE_VPN_PSK", "")
    gcp_ip = os.environ.get("BOOKFLOW_GCP_VPN_GW_IP", "0.0.0.0")
    gcp_psk = os.environ.get("BOOKFLOW_GCP_VPN_PSK", "")

    log.info(f"Azure VPN IP: {azure_ip} · PSK: {'***' if azure_psk else '(empty)'}")
    log.info(f"GCP VPN IP:   {gcp_ip} · PSK: {'***' if gcp_psk else '(empty)'}")

    # Tier 10 · VPC × 4
    for name, tmpl in VPCS:
        Stack(tier="10", name=name, template=tmpl).deploy()

    # Tier 10 · Customer Gateway (Azure + GCP)
    Stack(tier="10", name="customer-gateway",
          template="10-network-core/customer-gateway.yaml",
          parameters={"AzureVpnGatewayIp": azure_ip, "GcpHaVpnIp": gcp_ip}).deploy()

    # Tier 60 · TGW Hub
    Stack(tier="60", name="tgw",
          template="60-network-cross-cloud/tgw.yaml").deploy()

    # Tier 60 · VPC RT routes → TGW (cross-VPC + cross-cloud)
    Stack(tier="60", name="tgw-vpc-routes",
          template="60-network-cross-cloud/tgw-vpc-routes.yaml").deploy()

    # Tier 60 · Site-to-Site VPN
    vpn_params = {}
    if azure_ip != "0.0.0.0":
        vpn_params["EnableAzureVpn"] = "true"
        if azure_psk:
            vpn_params["AzurePresharedKey"] = azure_psk
    if gcp_ip != "0.0.0.0":
        vpn_params["EnableGcpVpn"] = "true"
        if gcp_psk:
            vpn_params["GcpPresharedKey"] = gcp_psk

    if not vpn_params:
        log.warn("Azure / GCP VPN IP env var   · VPN connection skip")
    else:
        Stack(tier="60", name="vpn-site-to-site",
              template="60-network-cross-cloud/vpn-site-to-site.yaml",
              parameters=vpn_params).deploy()
        _attach_vpn_to_tgw_rt()

    log.step("=== cross-cloud deploy  · BGP propagation  ~5-10   ===")


def _attach_vpn_to_tgw_rt() -> None:
    """VPN attachment  TGW route table  association + propagation."""
    import boto3
    from ..lib.config import Config
    ec2 = boto3.client("ec2", region_name=Config.REGION)

    tgw_rt_id = Stack(tier="60", name="tgw", template="").outputs().get("TgwRouteTableId")
    if not tgw_rt_id:
        log.warn("TGW route table id   · propagation skip")
        return

    # VPN attachment  (VPN connection    attachment)
    attachments = ec2.describe_transit_gateway_attachments(
        Filters=[{"Name": "resource-type", "Values": ["vpn"]},
                 {"Name": "state", "Values": ["available", "pending"]}]
    )["TransitGatewayAttachments"]

    for att in attachments:
        att_id = att["TransitGatewayAttachmentId"]
        name = next((t["Value"] for t in att.get("Tags", []) if t["Key"] == "Name"), "?")

        # Association
        try:
            ec2.associate_transit_gateway_route_table(
                TransitGatewayRouteTableId=tgw_rt_id,
                TransitGatewayAttachmentId=att_id,
            )
            log.info(f"  associate {att_id} ({name}) → tgw-rt")
        except ec2.exceptions.ClientError as e:
            if "already associated" in str(e) or "Resource.AlreadyAssociated" in str(e):
                pass
            else:
                log.warn(f"  associate fail: {e}")

        # Propagation (BGP route )
        try:
            ec2.enable_transit_gateway_route_table_propagation(
                TransitGatewayRouteTableId=tgw_rt_id,
                TransitGatewayAttachmentId=att_id,
            )
            log.info(f"  propagate {att_id} ({name}) → tgw-rt")
        except ec2.exceptions.ClientError as e:
            if "already" in str(e).lower():
                pass
            else:
                log.warn(f"  propagate fail: {e}")


def destroy() -> None:
    log.step("=== cross-cloud destroy ===")
    Stack(tier="60", name="vpn-site-to-site", template="").destroy()
    Stack(tier="60", name="tgw-vpc-routes", template="").destroy()
    Stack(tier="60", name="tgw", template="").destroy()
    Stack(tier="10", name="customer-gateway", template="").destroy()
    for name, _tmpl in reversed(VPCS):
        Stack(tier="10", name=name, template="").destroy()
    log.step("=== cross-cloud destroy  ===")
