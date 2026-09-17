"""Issue/renew the Compose proxy certificate; suitable for a host cron job."""

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def compose(*args, capture=False):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )


def manage(action, *, dry_run=False):
    # Let Compose interpret .env and environment overrides; never source .env as shell code.
    config = json.loads(compose("config", "--format", "json", capture=True).stdout)
    domain = config["services"]["proxy"]["environment"]["SITE_ADDRESS"]
    if (
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", domain)
        or "." not in domain
        or ".." in domain
        or domain.endswith((".localhost", ".local", ".test", ".invalid", ".example"))
    ):
        raise ValueError("SITE_ADDRESS에 실제 외부 DNS 도메인을 설정하세요.")

    if action == "issue":
        # Profile services are included only when their profile is enabled.
        tls_config = json.loads(
            compose("--profile", "tls", "config", "--format", "json", capture=True).stdout
        )
        email = tls_config["services"]["certbot"]["environment"]["ACME_EMAIL"]
        if not email or "@" not in email:
            raise ValueError("ACME_EMAIL에 인증서 등록용 이메일을 설정하세요.")
        compose("up", "-d", "--wait", "proxy")
        arguments = [
            "certonly",
            "--webroot",
            "--webroot-path",
            "/var/www/certbot",
            "--cert-name",
            domain,
            "--domain",
            domain,
            "--email",
            email,
            "--agree-tos",
            "--non-interactive",
            "--keep-until-expiring",
        ]
    else:
        arguments = ["renew", "--cert-name", domain, "--non-interactive"]
    if dry_run:
        arguments.append("--dry-run")
    compose("run", "--rm", "--no-deps", "certbot", *arguments)
    if dry_run:
        return
    if action == "issue":
        # Select the HTTPS template now that the certificate exists.
        compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "proxy")
    else:
        # Never reload on renewal failure; nginx keeps serving its existing certificate.
        compose("exec", "-T", "proxy", "nginx", "-t")
        compose("exec", "-T", "proxy", "nginx", "-s", "reload")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("issue", "renew"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        manage(args.action, dry_run=args.dry_run)
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"TLS 작업 실패: {exc}\n")


if __name__ == "__main__":
    main()
