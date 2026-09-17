import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts import manage_tls


@pytest.fixture
def tls_commands(monkeypatch):
    calls = []
    config = {
        "services": {
            "proxy": {"environment": {"SITE_ADDRESS": "chat.example.com"}},
            "certbot": {"environment": {"ACME_EMAIL": "admin@example.com"}},
        }
    }

    def compose(*args, capture=False):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps(config))

    monkeypatch.setattr(manage_tls, "compose", compose)
    return calls, config


def test_tls_requires_domain_and_registration_email(tls_commands):
    calls, config = tls_commands
    config["services"]["proxy"]["environment"]["SITE_ADDRESS"] = "localhost"
    with pytest.raises(ValueError, match="SITE_ADDRESS"):
        manage_tls.manage("issue")
    assert not any("run" in call for call in calls)
    config["services"]["proxy"]["environment"]["SITE_ADDRESS"] = "chat.example.com"
    config["services"]["certbot"]["environment"]["ACME_EMAIL"] = ""
    with pytest.raises(ValueError, match="ACME_EMAIL"):
        manage_tls.manage("issue")
    assert not any("up" in call for call in calls)


def test_dry_run_never_activates_test_certificates(tls_commands):
    calls, _ = tls_commands
    manage_tls.manage("issue", dry_run=True)
    assert any("--dry-run" in call for call in calls)
    assert not any("--force-recreate" in call or "reload" in call for call in calls)


def test_failed_renewal_does_not_reload_proxy(tls_commands, monkeypatch):
    calls, _ = tls_commands
    original = manage_tls.compose

    def fail(*args, **kwargs):
        if "renew" in args:
            raise subprocess.CalledProcessError(1, ["certbot", "renew"])
        return original(*args, **kwargs)

    monkeypatch.setattr(manage_tls, "compose", fail)
    with pytest.raises(subprocess.CalledProcessError):
        manage_tls.manage("renew")
    assert not any("reload" in call for call in calls)
