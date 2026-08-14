#!/usr/bin/env python3
"""Reject unintended user-facing references to the former product name."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = (ROOT,)
TEXT_SUFFIXES = {".html", ".ini", ".json", ".md", ".py", ".sh", ".ts", ".tsx", ".yaml", ".yml"}
EXCLUDED_PARTS = {
    ".git",
    ".next",
    ".venv",
    "_internal-repo-staging",
    "build",
    "node_modules",
    "work",
}
FORMER_NAME = "nest" + "ledger"

ALLOWED = {
    "apps/api/app/data_management.py": ("LEGACY_ARCHIVE_FORMATS",),
    "apps/api/tests/test_health.py": ("legacy_manifest", 'restored.json()["manifest"]'),
    "docs/architecture/BRAND_AND_COMPATIBILITY.md": ("archive import accepts the former",),
}

REQUIRED_ASSETS = (
    "assets/brand/tallystead/tallystead-icon.svg",
    "assets/brand/tallystead/tallystead-icon-dark.svg",
    "assets/brand/tallystead/tallystead-horizontal.svg",
    "assets/brand/tallystead/tallystead-horizontal-dark.svg",
    "assets/brand/tallystead/tallystead-lockup.svg",
    "assets/brand/tallystead/tallystead-lockup-dark.svg",
    "assets/brand/tallystead/web-icons/favicon.svg",
    "assets/brand/tallystead/web-icons/favicon.ico",
    "assets/brand/tallystead/web-icons/apple-touch-icon.png",
    "assets/brand/tallystead/web-icons/pwa-192x192.png",
    "assets/brand/tallystead/web-icons/pwa-512x512.png",
    "assets/brand/tallystead/web-icons/pwa-maskable-192x192.png",
    "assets/brand/tallystead/web-icons/pwa-maskable-512x512.png",
    "assets/brand/tallystead/web-icons/safari-pinned-tab.svg",
)


def files():
    for target in SCAN:
        if target.is_file():
            yield target
            continue
        for path in target.rglob("*"):
            if path.is_file() and path.suffix in TEXT_SUFFIXES and not EXCLUDED_PARTS.intersection(path.parts):
                yield path


def main() -> int:
    failures: list[str] = []
    for relative in REQUIRED_ASSETS:
        if not (ROOT / relative).is_file():
            failures.append(f"{relative}: required Tallystead asset is missing")
    for path in files():
        relative = path.relative_to(ROOT).as_posix()
        allowed = ALLOWED.get(relative, ())
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FORMER_NAME in line.lower() and not any(value in line for value in allowed):
                failures.append(f"{relative}:{number}: {line.strip()}")
    if failures:
        print("Unapproved former-brand references found:")
        print("\n".join(failures))
        return 1
    print("Tallystead brand reference check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
