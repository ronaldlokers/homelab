# The ingress rules decide what is reachable from the internet.
#
# Two other tunnels were deleted rather than imported: linkdingkube (a live
# route to linkding, no connectors) and "Familie C. Lokers" (no config).
resource "cloudflare_zero_trust_tunnel_cloudflared" "homelab_prod" {
  account_id = var.account_id
  name       = "homelab-prod"
  config_src = "cloudflare"
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "homelab_prod" {
  account_id = var.account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.homelab_prod.id

  config = {
    ingress = [
      # The only service published to the internet.
      {
        hostname = "ntfy.ronaldlokers.nl"
        service  = "http://ntfy.ntfy.svc.cluster.local:80"
      },
      # Everything else is refused at the edge.
      {
        service = "http_status:404"
      },
    ]
  }
}

import {
  to = cloudflare_zero_trust_tunnel_cloudflared.homelab_prod
  id = "${var.account_id}/54ea9047-19a8-4e85-8d00-8de688b7c873"
}
import {
  to = cloudflare_zero_trust_tunnel_cloudflared_config.homelab_prod
  id = "${var.account_id}/54ea9047-19a8-4e85-8d00-8de688b7c873"
}
