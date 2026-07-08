---
name: warn-validate-before-push
enabled: true
event: bash
pattern: git\s+push
action: warn
---

⚠️ **Validated before this push?**

This repo's CI (`.github/workflows/validate.yaml`) runs `scripts/validate.sh` on every PR — catch schema errors before push, not after.

If manifests changed and `/validate` hasn't run clean since, run it now before proceeding.
