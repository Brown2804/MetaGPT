# Codex OAuth Bridge for MetaGPT

This repository now includes a **usable local MetaGPT + Codex OAuth service flow**, not just a raw bridge script.

## What it does

- Exposes `POST /v1/chat/completions`
- Supports:
  - normal JSON completions
  - SSE streaming completions
  - non-stream tool-call compatibility (`function.arguments` as JSON string)
- Reads Codex OAuth credentials from either:
  - incoming `Authorization: Bearer <jwt>` header, or
  - OpenClaw auth store fallback: `~/.openclaw/agents/main/agent/auth-profiles.json`
- Automatically refreshes OpenAI/Codex OAuth tokens when they are close to expiry

## The practical user flow

From the repo root:

```bash
cd /home/khj12/.openclaw/workspace/research/MetaGPT
./scripts/metagpt-codex-service.sh ensure
```

What `ensure` does:

1. imports `openai-codex:default` from OpenClaw into `~/.metagpt/auth/auth-profiles.json`
2. refreshes the token if needed
3. writes `~/.metagpt/config2.yaml` so MetaGPT points at the local bridge
4. starts the bridge in the background

After that, the service is usable with normal MetaGPT commands:

```bash
./scripts/metagpt-codex.sh startup "Create a hello world script" \
  --n-round 1 \
  --code-review false \
  --run-tests false \
  --implement false \
  --project-name codex_bridge_smoke
```

## Operations

### Setup/configure only

```bash
./scripts/metagpt-codex-service.sh setup
```

### Ensure configured + running

```bash
./scripts/metagpt-codex-service.sh ensure
```

### Start bridge only

```bash
./scripts/metagpt-codex-service.sh start
```

### Stop bridge

```bash
./scripts/metagpt-codex-service.sh stop
```

### Status

```bash
./scripts/metagpt-codex-service.sh status
```

### Logs

```bash
./scripts/metagpt-codex-service.sh logs --lines 120
```

### HTTP smoke test

```bash
./scripts/metagpt-codex-service.sh smoke
```

### Full live validation

This runs the full user-facing service matrix end-to-end:

```bash
./scripts/metagpt-local.sh python scripts/test-codex-service-live.py
```

## Direct bridge usage

If you want to run only the raw bridge:

```bash
cd /home/khj12/.openclaw/workspace/research/MetaGPT
./scripts/codex-bridge-local.sh --port 8787 --cwd "$PWD"
```

Health check:

```bash
curl http://127.0.0.1:8787/health
```

## MetaGPT config written by setup/ensure

`~/.metagpt/config2.yaml`

```yaml
llm:
  api_type: "openai_codex"
  auth_mode: "oauth_profile"
  auth_profile: "openai-codex:default"
  base_url: "http://127.0.0.1:8787/v1"
  api_key: ""
  model: "gpt-5.4"
  stream: true
```

## Manual auth import

If you want to import auth yourself:

```bash
cd /home/khj12/.openclaw/workspace/research/MetaGPT
./scripts/metagpt-local.sh auth import-openclaw-codex default \
  /home/khj12/.openclaw/agents/main/agent/auth-profiles.json \
  openai-codex:default
```

## Exposed endpoints

- `GET /health`
- `POST /v1/chat/completions`

`/` intentionally does not provide the API surface. Use `/health` for checks.

## Notes

- The most convenient entrypoint is `./scripts/metagpt-codex.sh ...` because it automatically runs `ensure` first.
- The bridge uses Codex app-server external auth mode under the hood.
- Embeddings are still separate from Codex OAuth; this bridge is for chat/completions compatibility.
