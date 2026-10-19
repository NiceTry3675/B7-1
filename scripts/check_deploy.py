"""Read-only deployment preflight; never prints resolved environment values."""

import argparse
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from app.config import Settings
from scripts.compose_support import Check, configuration, report


def public_domain(value):
    labels = value.split(".")
    return (
        1 < len(labels)
        and len(value) <= 253
        and all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", s) for s in labels)
        and not labels[-1].isdigit()
        and labels[-1].lower() not in {"localhost", "local", "test", "invalid", "example"}
    )


def validate(config, *, allow_unconfigured_llm=False):
    services = config.get("services", {})
    required = {"backend", "frontend", "proxy", "certbot"}
    if not required.issubset(services):
        return [Check("services", False, "backend/frontend/proxy/certbot 서비스가 필요합니다.")]
    backend = services["backend"]
    env = backend.get("environment", {})
    proxy_env = services["proxy"].get("environment", {})
    tls_env = services["certbot"].get("environment", {})
    checks = [
        Check(
            "domain",
            public_domain(str(proxy_env.get("SITE_ADDRESS", ""))),
            "SITE_ADDRESS는 공인 DNS 도메인이어야 합니다. DNS 연결 자체는 별도 확인하세요.",
        ),
        Check(
            "acme_email",
            bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(tls_env.get("ACME_EMAIL", "")))),
            "ACME_EMAIL 인증서 등록 이메일을 확인합니다.",
        ),
        Check(
            "private_ports",
            not backend.get("ports") and not services["frontend"].get("ports"),
            "백엔드·프론트 포트는 호스트에 공개하지 않습니다.",
        ),
    ]
    try:
        settings = Settings(_env_file=None, **{key.lower(): value for key, value in env.items()})
    except ValueError:
        checks.append(
            Check("backend_settings", False, "백엔드 설정과 시간 제한 순서를 확인하세요.")
        )
        return checks
    frontend_timeout = services["frontend"].get("environment", {}).get("BACKEND_TIMEOUT_SECONDS")
    try:
        consistent = (
            float(frontend_timeout) == settings.backend_timeout_seconds
            and float(proxy_env.get("PROXY_TIMEOUT_SECONDS")) == settings.proxy_timeout_seconds
        )
    except (TypeError, ValueError):
        consistent = False
    checks.append(
        Check("timeouts", consistent, "프론트·프록시의 실제 제한과 백엔드 설정 일치 여부.")
    )
    data = [v for v in backend.get("volumes", []) if v.get("target") == "/data"]
    checks.append(
        Check(
            "database_volume",
            bool(data)
            and data[0].get("type") in {"bind", "volume"}
            and not data[0].get("read_only", False)
            and settings.database_url == "sqlite:////data/chat.db",
            "DB는 쓰기 가능한 /data 영속 마운트의 chat.db를 사용합니다.",
        )
    )
    if allow_unconfigured_llm and not settings.llm_base_url and not settings.llm_service_token:
        checks.append(Check("llm", True, "UI 전용 배포 선택: AI 질문은 설정 오류로 실패합니다."))
        return checks
    try:
        url = urlsplit(settings.llm_base_url)
        connection = (
            url.scheme in {"http", "https"}
            and bool(url.hostname)
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and url.path in {"", "/"}
        )
    except ValueError:
        connection = False
    token = settings.llm_service_token
    checks.append(
        Check(
            "llm_connection",
            bool(connection)
            and token.isascii()
            and len(token) >= 32
            and not any(c.isspace() for c in token),
            "LLM 기본 주소와 공백 없는 32자 이상 ASCII 토큰 형식만 확인합니다.",
        )
    )
    mounts = [
        v for v in backend.get("volumes", []) if v.get("target") == settings.llm_tokenizer_path
    ]
    readable = False
    if mounts and mounts[0].get("type") == "bind":
        path = Path(mounts[0]["source"])
        readable = path.is_dir() and os.access(path, os.R_OK | os.X_OK)
        readable = readable and (path / "tokenizer_config.json").is_file()
        readable = readable and any(
            (path / name).is_file()
            for name in (
                "tokenizer.json",
                "tokenizer.model",
                "spiece.model",
                "vocab.json",
                "vocab.txt",
            )
        )
    checks.append(
        Check(
            "tokenizer_files",
            readable,
            "호스트 토크나이저 파일을 확인합니다. 템플릿·모델 일치는 별도 검증 대상입니다.",
        )
    )
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-unconfigured-llm", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        checks = validate(configuration(), allow_unconfigured_llm=args.allow_unconfigured_llm)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        checks = [Check("compose", False, "Docker Engine/Compose 접근 권한과 설정을 확인하세요.")]
    raise SystemExit(report(checks, as_json=args.json))


if __name__ == "__main__":
    main()
