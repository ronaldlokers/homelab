---
name: validate
description: >
  Validate all Kubernetes manifests and Kustomize overlays in this repo against their schemas
  by running scripts/validate.sh. Use before pushing infrastructure/app changes, when the user
  asks to "validate manifests", "check the yaml", "run validate", or invokes /validate. Also use
  proactively after editing files under clusters/, infrastructure/, apps/, or monitoring/.
---

Run `./scripts/validate.sh` from the repo root and report the result.

## What it does

- Validates every manifest under `clusters/` directly
- Builds every `kustomization.yaml` in the repo with `kustomize build` and validates the output with `kubeconform -strict`
- Schemas are bulk-fetched once per run (Flux CRDs, core Kubernetes schemas) so it runs offline after the first download — no per-resource network calls

## On failure

Report the failing file/resource and the exact kubeconform error. Do not guess at a fix without reading the offending manifest — the error message names the resource kind and the specific schema violation (missing required field, wrong type, unknown field under `-strict`).

## Notes

- SOPS-encrypted `Secret` resources are skipped (`-skip=Secret`) since their `sops:` metadata field fails strict schema validation by design — this is expected, not a bug
- This is the same check CI runs in `.github/workflows/validate.yaml` on every PR
