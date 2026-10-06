"""Shared, quiet access to the effective deployment configuration."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def compose(*args, timeout=60):
    # Captured output can contain secrets. Callers must not print it on failure.
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def configuration():
    value = json.loads(compose("--profile", "tls", "config", "--format", "json").stdout)
    if not isinstance(value, dict) or not isinstance(value.get("services"), dict):
        raise ValueError("Compose configuration is invalid")
    return value


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    message: str


def report(checks, *, as_json=False):
    if as_json:
        print(json.dumps([vars(check) for check in checks], ensure_ascii=False))
    else:
        for check in checks:
            print(f"{'PASS' if check.ok else 'FAIL'} {check.name}: {check.message}")
    return 0 if all(check.ok for check in checks) else 1
