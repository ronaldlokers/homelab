#!/usr/bin/env bash
# Validates all Kubernetes manifests and kustomize overlays with kubeconform.
# Based on https://github.com/fluxcd/flux2-kustomize-helm-example/blob/main/scripts/validate.sh
#
# Prerequisites: kustomize, kubeconform
# Flux CRD schemas are downloaded to /tmp/flux-crd-schemas on each run.

set -euo pipefail

# SOPS-encrypted Secrets carry a top-level `sops:` field that fails strict
# validation, so Secrets are skipped entirely.
kubeconform_flags=("-skip=Secret")
kubeconform_config=(
  "-strict"
  "-ignore-missing-schemas"
  "-schema-location" "default"
  "-schema-location" "/tmp/flux-crd-schemas"
  "-schema-location" "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
  "-summary"
)

if [[ ! -d /tmp/flux-crd-schemas/master-standalone-strict ]]; then
  echo "INFO - Downloading Flux OpenAPI schemas"
  mkdir -p /tmp/flux-crd-schemas/master-standalone-strict
  curl -sL https://github.com/fluxcd/flux2/releases/latest/download/crd-schemas.tar.gz |
    tar zxf - -C /tmp/flux-crd-schemas/master-standalone-strict
fi

echo "INFO - Validating cluster manifests"
find ./clusters -maxdepth 2 -type f -name '*.yaml' -not -name '.sops.yaml' -print0 |
  xargs -0 kubeconform "${kubeconform_flags[@]}" "${kubeconform_config[@]}"

echo "INFO - Validating kustomize overlays"
find . -type f -name kustomization.yaml -not -path "./.git/*" -print0 |
  while IFS= read -r -d $'\0' file; do
    dir="$(dirname "$file")"
    echo "INFO - Building $dir"
    kustomize build "$dir" |
      kubeconform "${kubeconform_flags[@]}" "${kubeconform_config[@]}"
  done

echo "INFO - Validation passed"
