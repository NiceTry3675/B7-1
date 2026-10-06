from scripts.check_deploy import validate


def config(tmp_path):
    tokenizer = tmp_path / "tokenizer"
    tokenizer.mkdir()
    (tokenizer / "tokenizer_config.json").write_text("{}")
    (tokenizer / "tokenizer.json").write_text("{}")
    return {
        "services": {
            "backend": {
                "environment": {
                    "DATABASE_URL": "sqlite:////data/chat.db",
                    "LLM_BASE_URL": "http://llm.test",
                    "LLM_SERVICE_TOKEN": "x" * 32,
                    "LLM_TOKENIZER_PATH": "/tokenizer",
                },
                "volumes": [
                    {"type": "volume", "target": "/data", "source": "chat_data"},
                    {"type": "bind", "target": "/tokenizer", "source": str(tokenizer)},
                ],
            },
            "frontend": {"environment": {"BACKEND_TIMEOUT_SECONDS": "160"}},
            "proxy": {
                "environment": {
                    "SITE_ADDRESS": "chat.example.org",
                    "PROXY_TIMEOUT_SECONDS": "180",
                }
            },
            "certbot": {"environment": {"ACME_EMAIL": "admin@example.org"}},
        }
    }


def test_preflight_checks_effective_timeout_and_mounts(tmp_path):
    value = config(tmp_path)
    assert all(check.ok for check in validate(value))
    value["services"]["frontend"]["environment"]["BACKEND_TIMEOUT_SECONDS"] = "10"
    value["services"]["backend"]["volumes"][0]["read_only"] = True
    failures = {check.name for check in validate(value) if not check.ok}
    assert failures == {"timeouts", "database_volume"}


def test_llm_must_be_configured_unless_explicitly_disabled(tmp_path):
    value = config(tmp_path)
    env = value["services"]["backend"]["environment"]
    env["LLM_BASE_URL"] = env["LLM_SERVICE_TOKEN"] = ""
    assert not all(check.ok for check in validate(value))
    assert all(check.ok for check in validate(value, allow_unconfigured_llm=True))
    env["LLM_SERVICE_TOKEN"] = "secret-do-not-print"
    checks = validate(value, allow_unconfigured_llm=True)
    assert not all(check.ok for check in checks)
    assert "secret-do-not-print" not in repr(checks)
