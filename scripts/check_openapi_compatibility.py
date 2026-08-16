#!/usr/bin/env python3
"""Detect common breaking changes between two Tallystead OpenAPI contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put"}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def signature(schema: dict[str, Any]) -> str:
    type_shape = {
        key: schema[key]
        for key in ("$ref", "allOf", "anyOf", "format", "items", "nullable", "oneOf", "type")
        if key in schema
    }
    return json.dumps(type_shape, sort_keys=True)


def compare_schema(old: dict[str, Any], new: dict[str, Any], location: str, failures: list[str]) -> None:
    if signature(old) != signature(new):
        failures.append(f"{location}: type, format, reference, or nullability changed")
    old_enum = set(old.get("enum", []))
    new_enum = set(new.get("enum", []))
    if old_enum - new_enum:
        failures.append(f"{location}: enum values removed: {sorted(old_enum - new_enum)}")


def parameters(operation: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (parameter.get("in", ""), parameter.get("name", "")): parameter
        for parameter in operation.get("parameters", [])
    }


def compare_operation(
    old: dict[str, Any], new: dict[str, Any], location: str, failures: list[str]
) -> None:
    old_parameters = parameters(old)
    new_parameters = parameters(new)
    for key, old_parameter in old_parameters.items():
        if key not in new_parameters:
            failures.append(f"{location}: parameter {key[0]}:{key[1]} was removed")
            continue
        new_parameter = new_parameters[key]
        if not old_parameter.get("required") and new_parameter.get("required"):
            failures.append(f"{location}: parameter {key[0]}:{key[1]} became required")
        compare_schema(
            old_parameter.get("schema", {}),
            new_parameter.get("schema", {}),
            f"{location} parameter {key[0]}:{key[1]}",
            failures,
        )
    for key, new_parameter in new_parameters.items():
        if key not in old_parameters and new_parameter.get("required"):
            failures.append(f"{location}: new required parameter {key[0]}:{key[1]}")

    old_body = old.get("requestBody")
    new_body = new.get("requestBody")
    if old_body and not new_body:
        failures.append(f"{location}: request body was removed")
    elif old_body and new_body:
        if not old_body.get("required") and new_body.get("required"):
            failures.append(f"{location}: request body became required")
        for media_type in old_body.get("content", {}):
            if media_type not in new_body.get("content", {}):
                failures.append(f"{location}: request media type {media_type} was removed")
    elif new_body and new_body.get("required"):
        failures.append(f"{location}: a required request body was added")

    old_success = {
        code: response
        for code, response in old.get("responses", {}).items()
        if str(code).startswith("2")
    }
    new_responses = new.get("responses", {})
    for code, old_response in old_success.items():
        if code not in new_responses:
            failures.append(f"{location}: successful response {code} was removed")
            continue
        new_response = new_responses[code]
        for media_type in old_response.get("content", {}):
            if media_type not in new_response.get("content", {}):
                failures.append(
                    f"{location}: response {code} media type {media_type} was removed"
                )


def breaking_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for path, old_path in old.get("paths", {}).items():
        if path not in new.get("paths", {}):
            failures.append(f"{path}: path was removed")
            continue
        new_path = new["paths"][path]
        for method, old_operation in old_path.items():
            if method.lower() not in HTTP_METHODS:
                continue
            location = f"{method.upper()} {path}"
            if method not in new_path:
                failures.append(f"{location}: operation was removed")
                continue
            compare_operation(old_operation, new_path[method], location, failures)

    old_schemas = old.get("components", {}).get("schemas", {})
    new_schemas = new.get("components", {}).get("schemas", {})
    for name, old_schema in old_schemas.items():
        location = f"schema {name}"
        if name not in new_schemas:
            failures.append(f"{location}: component was removed")
            continue
        new_schema = new_schemas[name]
        compare_schema(old_schema, new_schema, location, failures)
        old_properties = old_schema.get("properties", {})
        new_properties = new_schema.get("properties", {})
        for property_name, old_property in old_properties.items():
            if property_name not in new_properties:
                failures.append(f"{location}.{property_name}: property was removed")
                continue
            compare_schema(
                old_property,
                new_properties[property_name],
                f"{location}.{property_name}",
                failures,
            )
        added_required = set(new_schema.get("required", [])) - set(old_schema.get("required", []))
        if added_required:
            failures.append(f"{location}: required properties added: {sorted(added_required)}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    failures = breaking_changes(load(args.baseline), load(args.candidate))
    if failures:
        print("Breaking OpenAPI changes detected:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print("No common breaking OpenAPI changes detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
