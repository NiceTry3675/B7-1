# Raspberry Pi llama.cpp adapter

This branch adds the project's `/internal/v1` API on the Raspberry Pi. The
adapter talks to an already running `llama-server` over loopback HTTP. It never
starts, stops, builds, downloads, or changes the model process. The bundled
Vulkan patch is a separate reviewable file for a llama.cpp checkout.

## Runtime boundary

```text
B7-1 FastAPI -> private tunnel/VPN -> rpi_llm adapter :8001
                                      -> loopback llama-server :8080
                                      -> Gemma 3 1B GGUF on RPi5 CPU
```

The verified public HTTPS route is an alternative to the backend's private VPN
connection. See the [current status and AWS settings](STATUS.md): the caller
needs only HTTPS and the service key, while Pi5 continues running Tailscale Funnel.
The tablet's existing private route has not been changed.

- Run one adapter process. A Linux process lease rejects a second adapter for
  the same runtime URL. Run llama-server with `--parallel 1` and a loopback bind.
- The adapter admits one job and queues zero jobs. A second request receives
  `503 LLM_BUSY` and `Retry-After: 5` without waiting.
- The [CTS state/action table](CTS.md) separates a timed-out active worker
  from an uncertain finished worker and classifies every missing edge.
- The model is checked with `/health`, `/props`, and `/v1/models`. The GGUF
  path, alias, context size, and one server slot must match the configuration.
  The raw server slot must also be idle before the adapter admits a new job.
  `/slots` can omit `id_task` before the first job or retain an earlier task ID;
  `is_processing=false` is the idle signal.
- Token counting uses llama-server's
  `/v1/chat/completions/input_tokens` with the same system/history messages as
  generation. Input plus requested output must fit the configured context.
- Client disconnects and wrapper deadlines do not cancel a running job. The
  slot remains occupied until the job finishes. An uncertain runtime transport
  failure quarantines the slot. It returns to ready only after `/slots` reports
  an idle single slot and the model checks pass.
- Prompts, answers, service tokens, and internal URLs are not written to
  adapter application logs. The B7-1 backend retains the conversation in SQLite.

## Configuration

Create `.env` from `.env.example` locally. Do not commit `.env` or GGUF files.
The two services use the same ASCII `LLM_SERVICE_TOKEN` (for example,
`openssl rand -hex 32`, which encodes 32 random bytes) and
`LLM_MODEL` alias. The cloud/backend sets `LLM_BASE_URL` to the private adapter
address or the authenticated public HTTPS origin, without `/internal/v1`.
The Raspberry Pi adapter reads:

| Variable | Meaning |
| --- | --- |
| `LLM_MODEL_PATH` | Absolute path of the already installed Gemma 3 1B GGUF |
| `LLM_RUNTIME_URL` | `http://127.0.0.1:8080` or another loopback origin |
| `LLM_CONTEXT_TOKENS` | Exactly the llama-server `--ctx-size` value, initially 4096 |
| `LLM_OUTPUT_TOKEN_LIMIT` | Adapter ceiling, initially 512 |
| `LLM_GENERATION_TIMEOUT_SECONDS` | Complete internal request deadline, initially 120 |
| `LLM_RUNTIME_TIMEOUT_SECONDS` | Runtime HTTP timeout, initially 130 |
| `LLM_RUNTIME_PROBE_TIMEOUT_SECONDS` | Complete readiness/slot probe deadline, initially 5 |
| `LLM_MAX_CONCURRENCY` | Must be 1 |

HTTPX enforces network inactivity limits for connection, read, write, and
pool phases. The adapter also enforces a complete runtime-call deadline and
a shorter complete readiness/slot-probe deadline. A generation call that ends
with an uncertain timeout keeps the compute slot quarantined until runtime
idleness is verified.

For the chatbot's CPU run, use a locally built llama-server and
`--gpu-layers 0`. The GPU patches are separate research artifacts, not a
dependency of this command. The operator starts the model process separately.
Keep the raw port on loopback and use the service token as its API key.

```bash
(
umask 077
llm_api_key_file="$(mktemp)"
trap 'rm -f "$llm_api_key_file"' EXIT
printf '%s\n' "$LLM_SERVICE_TOKEN" > "$llm_api_key_file"
llama-server --model "$LLM_MODEL_PATH" --alias "$LLM_MODEL" \
  --ctx-size "$LLM_CONTEXT_TOKENS" --parallel 1 \
  --gpu-layers 0 \
  --host 127.0.0.1 --port 8080 --api-key-file "$llm_api_key_file" \
  --no-cache-prompt --slots --offline
)
```

With that server ready, start only the adapter:

```bash
.venv/bin/python -m scripts.rpi_llama_adapter --host 127.0.0.1 --port 8001
```

Bind to a VPN interface only when the private route is configured. Do not
publish the raw llama-server port. The backend still needs its local tokenizer
files and matching Gemma chat template for `ChatTokenCounter`; the adapter
rechecks the exact token count before inference.
The [local CPU server check](VERIFICATION.md#rpi5-실제-cpu-서버--2026-09-28) used port
18080 because port 8080 was occupied on this Pi; set `LLM_RUNTIME_URL` to the
same loopback port as the server.

## Patch provenance

`patches/llama.cpp/0001-*` carries local llama.cpp commit `b13d13e` against
base `1f368f3`. It detects V3D 7.x and changes only Broadcom Q4_0
single-token integer-dot matvec to two output rows per subgroup. `0002-*`
and `0003-*` are the separate VC7 small scalar and packed 4×8 dot tile
candidates. The Mesa V3D patch under `patches/mesa/` adds pre-spill RA and
dot-mode diagnostics. Their exact scope and tile arithmetic are in
[V3DV tile analysis](V3DV_TILE_ANALYSIS.md) and
[packed 4×8 dot analysis](V3DV_DOT4X8.md). None includes
the separate uncommitted Vulkan barrier edit in the original llama.cpp tree.
The patch scripts do a dry run by default, check SHA-256 digests, refuse dirty
target source, and need `--apply` to write another checkout. They never commit
or push there.

The earlier local RPi5 record in `docs/evidence/gemma-q4_0-brcm-summary.json`
reports 9/9 shape checks and four 20/20 repeated warm comparisons. Its short
Gemma tg8 sample changed from 0.277987 to 0.299188 tokens/s (about 7.6%).
That run used `-O0` host validation builds, two repetitions, and active desktop
graphics. It is historical evidence for this exact patch, not a fresh build,
long-context result, or production performance guarantee.

## Verification order

The first command uses mocks and in-process ASGI transports. It needs Python
dependencies already installed but makes no model or external HTTP calls.

```bash
bash scripts/verify_rpi_offline.sh
```

Then inspect the patch against a separate llama.cpp checkout. The first call
is read-only. The second is an explicit local source change, if needed:

```bash
.venv/bin/python -m scripts.apply_llama_patch --llama-dir /path/to/llama.cpp
.venv/bin/python -m scripts.apply_mesa_patch --mesa-dir /path/to/mesa
# Only in isolated checkouts, after inspecting the patches:
.venv/bin/python -m scripts.apply_llama_patch --llama-dir /path/to/llama.cpp --apply
.venv/bin/python -m scripts.apply_mesa_patch --mesa-dir /path/to/mesa --apply
git -C /path/to/llama.cpp diff --check
git -C /path/to/mesa diff --check
```

After the operator finishes the Gemma build and starts llama-server, check the
private adapter and backend link. On the Pi, provide the same local tokenizer
files used by the backend in `LLM_TOKENIZER_PATH` and compare the Transformers
chat template with llama-server's exact input-token count before generating. The
last command performs one real model request and is intentionally gated by
`--run`:

```bash
.venv/bin/python -m scripts.check_llm
.venv/bin/python -m scripts.check_tokenizer_match --run
.venv/bin/python -m scripts.check_rpi_generation --run
```

Finally verify two concurrent questions, runtime timeout/restart recovery,
SQLite records after restart, Gemma latency and memory, and the externally
reachable HTTPS UI on the actual deployment. The mock suite cannot establish
those hardware, network, or deployment properties.
