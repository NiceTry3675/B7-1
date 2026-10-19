# Deployment status — 2026-10-19

The S7 FE app and Pi5 Gemma connection passed live verification. The Pi5 adapter
is also available through public HTTPS for the planned AWS backend. The public
health checks were repeated at **2026-10-19 11:47 UTC**.

## Running configuration

| Component | Verified configuration |
| --- | --- |
| S7 FE | Termux/Debian 13; Gradio `:7860`, FastAPI `:8000`, local SQLite and tokenizer |
| Pi5 model | Gemma 3 1B Q4_0; llama.cpp `b13d13e`; CPU, 4 threads, context 4096, one slot |
| Pi5 adapter | Authenticated B7-1 `/internal/v1` API on `:8001`; llama-server on loopback `:18080` |
| Public adapter URL | `https://rpi5.tail61485f.ts.net` through Pi5 Tailscale Funnel |

The application code tested on S7 FE is `8b85b5b`; subsequent changes on this
branch document the results. The tablet results below are from the earlier run
on this date. Its SSH port refused connections during this status update, so
the tablet processes and checkout were not rechecked.

## AWS backend connection

Merge these settings into the backend environment. Replace the token placeholder
with the shared service key and use the backend's matching local tokenizer path.

```dotenv
LLM_BASE_URL=https://rpi5.tail61485f.ts.net
LLM_SERVICE_TOKEN=REPLACE_WITH_SHARED_SERVICE_TOKEN
LLM_MODEL=local-advisor
LLM_TOKENIZER_PATH=/absolute/path/to/gemma-tokenizer
LLM_CONTEXT_TOKENS=4096
LLM_MAX_OUTPUT_TOKENS=256
```

The backend needs the `tokenizer` dependency extra and matching tokenizer/chat
template files, but no model weights. Keep the existing timeout and app settings
from [`.env.example`](../.env.example). Check readiness with
`uv run --no-sync python -m scripts.check_llm` after configuring the environment.

- The AWS caller uses ordinary HTTPS and `Authorization: Bearer <service-key>`;
  it needs neither SSH nor a Tailscale client.
- Pi5 must keep Tailscale running because Funnel provides the public endpoint.
  See the [Funnel documentation](https://tailscale.com/docs/use-cases/application-testing/share-local-dev-server-with-internet).
- The S7 FE app's last verified configuration still uses the private Tailscale
  adapter address. It has not been switched to this public URL or tested with
  Tailscale disabled.
- SSH is not needed to keep the current Pi5 model and adapter services running.
  Those user services are transient; full startup after reboot is not configured
  or verified.

The private connection file is `~/.local/share/codyssey-b7-1/gemma-backend-connection.env`
on Pi5 and native S7 FE, with mode `0600`. In tablet Debian it is visible under
`/termux-home/.local/share/codyssey-b7-1/`. The actual key is distributed separately
and is not included in this branch.

## Verified results

- S7 FE: **136 tests passed, 4 skipped**; Ruff passed; 75 files passed formatting.
- Local tokenizer counts matched the Pi5 runtime for samples of 19 and 40 tokens.
- The real Front Controller → FastAPI → Pi5 Gemma → tablet SQLite flow passed:
  signup/login, two saved turns, history after login, UUID reuse and logout.
- Public HTTPS without a key returned `401 LLM_UNAUTHORIZED`; with the shared
  key it returned `200 ready`. A real generation returned `안녕하세요! 😊`.
- Both public relay addresses passed authenticated health checks again. The
  checks forced public destination IPs with `curl --noproxy '*' --resolve`, using
  normal TLS verification to bypass private MagicDNS/Tailscale routing. Configure
  the hostname above; relay IPs can change.

See the [tablet verification](VERIFICATION.md#s7-fe와-rpi5-실제-연결--2026-10-19),
[public HTTPS evidence](evidence/public-gemma-https-2026-10-19.json) and
[observed model response limitations](LIMITATIONS.md).

## Remaining checks

AWS deployment, a separate client with Tailscale disabled, tablet touch/layout,
and full process/reboot recovery remain unverified. The successful public route
test was run from Pi5 to public relay IPs, not from AWS. The initial public DNS
propagation delay is resolved. No hotspot switch or S24 gateway was used.
