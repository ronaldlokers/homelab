---
name: block-push-without-validate
enabled: true
event: bash
pattern: git\s+push
action: block
---

⚠️ **Run `/validate` before pushing this repo.**

This repo's CI (`.github/workflows/validate.yaml`) runs `scripts/validate.sh` on every PR — catch schema errors before push, not after.

Run the `validate` skill now. If it passes clean, retry `git push`. If this push has no manifest changes (docs-only, etc.), it's still cheap to run.
