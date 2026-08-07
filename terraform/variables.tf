variable "account_id" {
  description = "Cloudflare account that owns the tunnel"
  type        = string
  default     = "143df83f62a2529c370203066c03d61b"
}

variable "zone_id" {
  description = "Zone id for ronaldlokers.nl"
  type        = string
  default     = "fe2c1f71db3d91a712fe90eb2b5415a8"
}
