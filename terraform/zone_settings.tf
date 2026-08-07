# Without this Cloudflare accepts plain http from clients and proxies to the
# origin over https, so nothing in the cluster can tell (#304).
resource "cloudflare_zone_setting" "always_use_https" {
  zone_id    = var.zone_id
  setting_id = "always_use_https"
  value      = "on"
}

import {
  to = cloudflare_zone_setting.always_use_https
  id = "${var.zone_id}/always_use_https"
}
