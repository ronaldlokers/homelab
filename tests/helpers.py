#!/usr/bin/env python3
"""Loading the digest's modules, and the fixtures they are tested against.

The scripts live in `apps/base/campfire-nightscout-digest/` with hyphens in
their names because that is what the ConfigMap mounts, so they cannot be
imported the ordinary way.
"""

import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIGEST = ROOT / "apps" / "base" / "campfire-nightscout-digest"
GOLDEN = pathlib.Path(__file__).resolve().parent / "golden"

BANDS = (
    ("very low", 0, 53),
    ("low", 54, 69),
    ("in range", 70, 180),
    ("high", 181, 250),
    ("very high", 251, 10_000),
)


def load(name, **environment):
    """Import one of the digest's modules by path.

    Environment is applied before execution because these modules read their
    configuration at import time, which is how the real CronJob runs them.
    """
    import os

    previous = {key: os.environ.get(key) for key in environment}
    os.environ.update({key: value for key, value in environment.items()})
    try:
        sys.path.insert(0, str(DIGEST))
        spec = importlib.util.spec_from_file_location(
            name.replace("-", "_"), DIGEST / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(DIGEST))
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def fixture(name):
    """A committed fortnight, as [(label, [(minute, mg/dL), ...]), ...].

    The readings are stored rather than generated so that a port to another
    language can be held to the same numbers. A generator would have to be
    reimplemented to be reproduced, which is not a fixture, it is a second
    thing to keep in sync.
    """
    raw = json.loads((GOLDEN / f"{name}.json").read_text())
    return [(day["label"], [tuple(r) for r in day["readings"]]) for day in raw["days"]]


def expected(name):
    return json.loads((GOLDEN / f"{name}.findings.json").read_text())
