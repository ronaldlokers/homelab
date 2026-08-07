# The zone's A and CNAME records. TXT records are deliberately not managed
# here: they are verification and mail records issued by other services, and
# adopting them into terraform would mean this repo owns values it does not
# generate.
#
# ttl = 1 means "automatic", which is what the dashboard sets.

# Everything on the LAN answers on the MetalLB VIP through this wildcard.
# Immich returned to it when its tunnel hostname was removed (#304).
resource "cloudflare_dns_record" "wildcard" {
  zone_id = var.zone_id
  name    = "*.ronaldlokers.nl"
  type    = "A"
  content = "10.0.40.100"
  ttl     = 1
  proxied = false
}

resource "cloudflare_dns_record" "wildcard_private" {
  zone_id = var.zone_id
  name    = "*.private.ronaldlokers.nl"
  type    = "A"
  content = "10.0.40.100"
  ttl     = 1
  proxied = false
}

resource "cloudflare_dns_record" "wildcard_staging" {
  zone_id = var.zone_id
  name    = "*.staging.ronaldlokers.nl"
  type    = "A"
  content = "10.0.40.52"
  ttl     = 1
  proxied = false
}

# Outside the cluster, so it needs its own record rather than the wildcard.
resource "cloudflare_dns_record" "truenas" {
  zone_id = var.zone_id
  name    = "truenas.ronaldlokers.nl"
  type    = "A"
  content = "10.0.40.10"
  ttl     = 1
  proxied = false
}

# The only record that points anywhere public. Proxied, so Cloudflare
# terminates TLS and the origin is reached through the tunnel.
resource "cloudflare_dns_record" "ntfy" {
  zone_id = var.zone_id
  name    = "ntfy.ronaldlokers.nl"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.homelab_prod.id}.cfargotunnel.com"
  ttl     = 1
  proxied = true
}

import {
  to = cloudflare_dns_record.wildcard
  id = "${var.zone_id}/487060c232d131b2b8f062d105f40f34"
}
import {
  to = cloudflare_dns_record.wildcard_private
  id = "${var.zone_id}/636f5d1380220366f861d71f2427b6ee"
}
import {
  to = cloudflare_dns_record.wildcard_staging
  id = "${var.zone_id}/04cd0c06d2d8424d9823f095ac96e1eb"
}
import {
  to = cloudflare_dns_record.truenas
  id = "${var.zone_id}/54d75de26af81bd349ab5e9a51f63209"
}
import {
  to = cloudflare_dns_record.ntfy
  id = "${var.zone_id}/d737cc0ecf2e1c85a21c921b891151d0"
}
