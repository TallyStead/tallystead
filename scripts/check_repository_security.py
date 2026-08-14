#!/usr/bin/env python3
"""Fail CI when tracked files or workflows violate Tallystead's repository boundary."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".age", ".dump", ".key", ".p12", ".pfx", ".sql"}
FORBIDDEN_NAMES = {".env", "identity.txt"}
FORBIDDEN_PARTS = {"backup-keys", "backups", "import-samples", "minio-data", "postgres-data"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:EC |OPENSSH |RSA )?PRIVATE KEY-----"),
    re.compile(r"AGE-" r"SECRET-KEY-1[0-9A-Z]+"),
    re.compile(r"\bgh[opsu]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
RELEASE_WORKFLOW = Path(".github/workflows/release.yml")


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    )
    return [ROOT / item.decode() for item in output.split(b"\0") if item]


def main() -> int:
    failures: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(ROOT)
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES or FORBIDDEN_PARTS.intersection(relative.parts):
            failures.append(f"{relative}: forbidden sensitive/runtime artifact is tracked")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                failures.append(f"{relative}: matches a high-confidence credential pattern")
                break
        if relative.parts[:2] == (".github", "workflows"):
            if "pull_request_target:" in text:
                failures.append(f"{relative}: pull_request_target is prohibited")
            if re.search(r"uses:\s*actions/checkout@", text) and "persist-credentials: false" not in text:
                failures.append(f"{relative}: checkout must disable persisted credentials")
            for action_ref in re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", text, re.MULTILINE):
                if action_ref.startswith("./"):
                    continue
                revision = action_ref.rsplit("@", 1)[-1]
                if not re.fullmatch(r"[0-9a-f]{40}", revision):
                    failures.append(
                        f"{relative}: action {action_ref} is not pinned to an immutable commit SHA"
                    )
            write_permissions = re.findall(
                r"^\s+(attestations|contents|id-token|packages):\s*write\s*$", text, re.MULTILINE
            )
            if write_permissions and relative != RELEASE_WORKFLOW:
                failures.append(
                    f"{relative}: write permissions are reserved for the manual release workflow"
                )
            if relative == RELEASE_WORKFLOW:
                trigger_block = text.split("\npermissions:", maxsplit=1)[0]
                prohibited_triggers = re.findall(
                    r"^\s{2}(pull_request|push|release|schedule):", trigger_block, re.MULTILINE
                )
                if prohibited_triggers:
                    failures.append(
                        f"{relative}: release publishing must remain manual-only"
                    )
                required_release_controls = (
                    "workflow_dispatch:",
                    "confirm_release_checklist:",
                    "contents: write",
                    "packages: write",
                    "attestations: write",
                    "id-token: write",
                    "--draft",
                )
                for control in required_release_controls:
                    if control not in text:
                        failures.append(f"{relative}: missing release control {control}")
    if failures:
        print("Repository security policy failed:")
        print("\n".join(failures))
        return 1
    print("Tallystead repository security policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
