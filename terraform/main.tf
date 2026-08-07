# Cloudflare: tunnel ingress, DNS and zone settings (#308).
terraform {
  required_version = ">= 1.9"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
  }

  # Backblaze B2 over its S3 API. The skip_* flags are required: the AWS SDK
  # asserts things only true of AWS, and without skip_s3_checksum every write
  # fails. Credentials come from the environment — see README.md.
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

# Reads CLOUDFLARE_API_TOKEN.
provider "cloudflare" {}
