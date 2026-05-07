# WARNING:
# This HA VPN layer is a high-cost daily resource set for BOOKFLOW.
# Per the architecture rule, deploy only during 09:00-18:00 KST via start-day.sh
# and destroy after business hours via stop-day.sh.

resource "google_compute_ha_vpn_gateway" "bookflow_aws_ha_vpn" {
  name    = "bookflow-aws-ha-vpn"
  project = var.project_id
  region  = var.region
  network = data.google_compute_network.bookflow_vpc.id
}

resource "google_compute_external_vpn_gateway" "aws_tgw" {
  name    = "bookflow-aws-tgw-external-gw"
  project = var.project_id
  # Changed from FOUR_IPS_REDUNDANCY: AWS TGW provides two VPN tunnel
  # outside IPs for this HA VPN connection.
  redundancy_type = "TWO_IPS_REDUNDANCY"

  dynamic "interface" {
    # Keep exactly two AWS peer interfaces: id 0 and id 1.
    for_each = { for index, ip in var.aws_peer_ips : tostring(index) => ip }
    content {
      id         = tonumber(interface.key)
      ip_address = interface.value
    }
  }
}

resource "google_compute_vpn_tunnel" "aws_tunnels" {
  for_each = var.bgp_sessions

  name                            = "bookflow-aws-tunnel-${each.key}"
  project                         = var.project_id
  region                          = var.region
  vpn_gateway                     = google_compute_ha_vpn_gateway.bookflow_aws_ha_vpn.id
  vpn_gateway_interface           = each.value.vpn_gateway_interface
  peer_external_gateway           = google_compute_external_vpn_gateway.aws_tgw.id
  peer_external_gateway_interface = each.value.peer_external_gateway_interface
  router                          = google_compute_router.bookflow_aws_router.id
  shared_secret                   = var.vpn_shared_secret

  depends_on = [
    google_compute_router.bookflow_aws_router,
    google_compute_ha_vpn_gateway.bookflow_aws_ha_vpn,
    google_compute_external_vpn_gateway.aws_tgw,
  ]
}

resource "google_compute_router_interface" "aws_interfaces" {
  for_each = var.bgp_sessions

  name       = "bookflow-aws-if-${each.key}"
  project    = var.project_id
  region     = var.region
  router     = google_compute_router.bookflow_aws_router.name
  ip_range   = each.value.router_ip_cidr
  vpn_tunnel = google_compute_vpn_tunnel.aws_tunnels[each.key].name
}

resource "google_compute_router_peer" "aws_peers" {
  for_each = var.bgp_sessions

  name                      = "bookflow-aws-bgp-${each.key}"
  project                   = var.project_id
  region                    = var.region
  router                    = google_compute_router.bookflow_aws_router.name
  interface                 = google_compute_router_interface.aws_interfaces[each.key].name
  ip_address                = split("/", each.value.router_ip_cidr)[0]
  peer_ip_address           = each.value.peer_ip_address
  peer_asn                  = var.aws_tgw_bgp_asn
  advertised_route_priority = each.value.advertised_route_priority

  lifecycle {
    replace_triggered_by = [
      google_compute_router_interface.aws_interfaces[each.key],
    ]
  }
}
