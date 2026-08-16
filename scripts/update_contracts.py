#!/usr/bin/env python3
"""Refresh the canonical OpenAPI snapshot and generated TypeScript contract."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "contracts"


def run(*command: str, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    npm = shutil.which("npm")
    if npm is None:
        print("Node.js 22 and npm are required to generate Tallystead contracts.")
        return 1
    run(sys.executable, str(ROOT / "scripts" / "export_openapi.py"))
    run(npm, "ci", cwd=PACKAGE)
    run(npm, "run", "generate", cwd=PACKAGE)
    run(npm, "test", cwd=PACKAGE)
    run(npm, "run", "build", cwd=PACKAGE)
    print("Tallystead OpenAPI and TypeScript contracts are current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
