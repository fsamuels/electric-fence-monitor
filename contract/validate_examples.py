#!/usr/bin/env python3
"""Validate golden contract/example payloads against fence-state.schema.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("jsonschema is required: pip install jsonschema", file=sys.stderr)
    sys.exit(1)

CONTRACT_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = CONTRACT_DIR / "fence-state.schema.json"
EXAMPLES_DIR = CONTRACT_DIR / "examples"


def main() -> int:
    schema = json.loads(SCHEMA_PATH.read_text())
    validator = jsonschema.Draft202012Validator(schema)

    example_files = sorted(EXAMPLES_DIR.glob("*.json"))
    if not example_files:
        print(f"No example payloads found in {EXAMPLES_DIR}", file=sys.stderr)
        return 1

    failed = False
    for path in example_files:
        payload = json.loads(path.read_text())
        errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)
        if errors:
            failed = True
            print(f"FAIL {path.name}:")
            for err in errors:
                print(f"  - {err.message}")
        else:
            print(f"OK   {path.name}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
