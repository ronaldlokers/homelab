# Always Use HTTPS.
#
# Without it Cloudflare served ntfy and Immich over plain http and proxied the
# request to the origin over https, so the client saw 200 on an unencrypted
# connection and nothing in the cluster could tell (#304). It also broke
# Immich's OIDC login, because the page URL it built its redirect_uri from was
# http and authentik refused the mismatch.
resource "cloudflare_zone_setting" "always_use_https" {
  zone_id    = var.zone_id
  setting_id = "always_use_https"
  value      = "on"
}

import {
  to = cloudflare_zone_setting.always_use_https
  id = "${var.zone_id}/always_use_https"
}
