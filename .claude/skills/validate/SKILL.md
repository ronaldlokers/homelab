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

- Builds every `kustomization.yaml` in the repo with `kustomize build`, plus the manifests under `clusters/`, and validates the output with `kubeconform -strict`
- Schemas are bulk-fetched once (Flux CRDs, core Kubernetes, and a sparse checkout of the datreeio CRD catalog for the API groups this repo uses) so it runs fully offline — no per-resource network calls
- **Enforces coverage**: any resource that had no schema and so went unvalidated fails the run. It reports a validated percentage (currently ~93%; the remainder are Secrets and Flux's vendored CRDs)
- Checks that every `Secret` carrying a `data`/`stringData` payload is SOPS-encrypted

## On failure

Report the failing file/resource and the exact error. Do not guess at a fix without reading the offending manifest. Three distinct failure modes:

- **`failed validation`** — a real schema violation. The message names the kind and the problem (missing required field, wrong type, unknown field under `-strict`).
- **`had no schema and went unvalidated`** — the coverage gate. Nothing checked those resources. Usually the CRD catalog fetch failed or a new API group was introduced; check the "Fetching CRD schemas" line above it. Do **not** silence this by adding the kind to `ALLOWED_SKIP_KINDS` unless it genuinely has no schema anywhere.
- **`unencrypted data payload`** — a Secret was committed in plaintext. Encrypt it with SOPS before doing anything else.

## Notes

- SOPS-encrypted `Secret` resources are excluded from schema validation (`-skip=Secret`) since their `sops:` field fails strict validation by design. They are covered by the encryption check instead
- `kustomize build` does not render `HelmRelease` values, so chart-level mistakes are invisible here — those surface in the `flux-diff` workflow, which expands charts
- This is the same check CI runs in `.github/workflows/validate.yaml` on every PR
