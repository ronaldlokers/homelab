# The tunnel and, more importantly, its ingress rules.
#
# This is the part #308 is about. The rules decide what of this cluster is
# reachable from the internet, and they lived only in the Zero Trust dashboard:
# removing Immich's NetworkPolicy path in-cluster left it serving, because the
# hostname was still routed here and nothing in this repository said so.
#
# Two other tunnels existed alongside this one, both with zero connectors:
# "linkdingkube", which still carried a route to linkding, and
# "Familie C. Lokers". Both were deleted rather than imported — a tunnel with a
# live route and a valid credential is a standing exposure, whether or not
# anything currently runs it.
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
      # ntfy is the only service published to the internet. Its clients carry
      # tokens rather than browser sessions, which is why it stays out of the
      # forward-auth discussion in #295.
      {
        hostname = "ntfy.ronaldlokers.nl"
        service  = "http://ntfy.ntfy.svc.cluster.local:80"
      },
      # Anything not named above is refused here rather than at the origin.
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
