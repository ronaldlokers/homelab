#!/usr/bin/env python3
"""Exit 0 only if every YAML document in the file is a SOPS-encrypted Secret.

Used by the rendered-diff gate in flux-diff.yaml. flate omits Secret contents
from its render, so a change touching only encrypted Secrets can never produce a
rendered diff and is exempt from that gate.

The exemption has to be exact. Testing with `grep '^sops:'` only proves the file
CONTAINS an encrypted document, which a multi-document YAML satisfies while also
carrying an arbitrary manifest:

    kind: Secret        <- matches ^sops:
    sops: {...}
    ---
    kind: Deployment    <- smuggled past the gate

That turns the exemption into a way to bypass the gate entirely, which is the
opposite of its purpose. Anything that is not parseable, not a mapping, not a
Secret, or carries no top-level `sops` key fails closed.
"""
import sys

import yaml


def main() -> int:
    if len(sys.argv) != 2:
        return 1
    try:
        with open(sys.argv[1]) as fh:
            docs = [d for d in yaml.safe_load_all(fh) if d is not None]
    except (OSError, yaml.YAMLError):
        return 1

    if not docs:
        return 1

    for doc in docs:
        if not isinstance(doc, dict):
            return 1
        if doc.get("kind") != "Secret":
            return 1
        if "sops" not in doc:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
