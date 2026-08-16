#!/usr/bin/env python3
"""Generate or verify Tallystead's canonical, deterministic OpenAPI contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
DEFAULT_OUTPUT = ROOT / "packages" / "contracts" / "openapi" / "tallystead-v1.json"


def contract_document() -> dict:
    sys.path.insert(0, str(API_ROOT))
    from app.main import app

    schema = app.openapi()
    schema["x-tallystead-api-major"] = "v1"
    operation_ids = [
        operation.get("operationId")
        for path in schema["paths"].values()
        for method, operation in path.items()
        if method.lower() in {"delete", "get", "head", "options", "patch", "post", "put"}
    ]
    if any(operation_id is None for operation_id in operation_ids):
        raise RuntimeError("Every API operation must have an operationId")
    duplicates = sorted(
        {operation_id for operation_id in operation_ids if operation_ids.count(operation_id) > 1}
    )
    if duplicates:
        raise RuntimeError(f"Duplicate OpenAPI operation IDs: {', '.join(duplicates)}")
    return schema


def rendered_contract() -> str:
    return json.dumps(contract_document(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail instead of updating a stale contract")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    expected = rendered_contract()
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != expected:
            print(
                f"{output.relative_to(ROOT)} is stale; run "
                "`python scripts/export_openapi.py` and regenerate the TypeScript contract."
            )
            return 1
        print(f"{output.relative_to(ROOT)} matches the FastAPI contract.")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(expected, encoding="utf-8")
    print(f"Wrote {output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
