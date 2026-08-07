# Cloudflare configuration, which until now existed only in a dashboard.
#
# #308: the tunnel's ingress rules and the zone's DNS decide what of this
# estate is reachable from the internet, and neither was visible from this
# repository. Removing Immich's network path in-cluster left it serving anyway,
# because the hostname lived somewhere nothing here could see.
terraform {
  required_version = ">= 1.9"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
  }

  # State lives in Backblaze B2, reached through its S3-compatible API.
  #
  # The skip_* flags are not optional: the AWS SDK asserts things about the
  # endpoint that are only true of AWS. skip_s3_checksum in particular is the
  # one that bites — without it every write fails on a checksum header B2 does
  # not implement.
  #
  # Credentials come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY at run
  # time. They are a B2 application key restricted to this bucket, and they are
  # deliberately not in this repository — see README.md.
  backend "s3" {
    bucket = "lokilabs-homelab-terraform-state"
    key    = "cloudflare/terraform.tfstate"
    region = "eu-central-003"

    endpoints = {
      s3 = "https://s3.eu-central-003.backblazeb2.com"
    }

    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
    use_path_style              = true
  }
}

# The token is read from CLOUDFLARE_API_TOKEN. It is account-owned and scoped
# to DNS edit, zone settings edit and tunnel edit — no broader.
provider "cloudflare" {}
