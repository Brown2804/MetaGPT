# Codex OAuth Bridge for MetaGPT

This repository now includes a local OpenAI-compatible bridge backed by Codex OAuth via Codex app-server external auth mode.

## What it does

- Exposes `POST /v1/chat/completions`
- Supports:
  - normal JSON completions
  - SSE streaming completions
  - non-stream tool-call compatibility (`function.arguments` as JSON string)
- Reads Codex OAuth credentials from either:
  - incoming `Authorization: Bearer <jwt>` header, or
  - OpenClaw auth store fallback: `~/.openclaw/agents/main/agent/auth-profiles.json`

## Quick start

From the repo root:

```bash
cd /home/khj12/.openclaw/workspace/research/MetaGPT
./scripts/codex-bridge-local.sh --port 8787 --cwd "$PWD"
```

Health check:

```bash
curl http://127.0.0.1:8787/health
```

## Import Codex OAuth from OpenClaw into MetaGPT auth store

```bash
cd /home/khj12/.openclaw/workspace/research/MetaGPT
./scripts/metagpt-local.sh auth import-openclaw-codex default \
  /home/khj12/.openclaw/agents/main/agent/auth-profiles.json \
  openai-codex:default
```

## MetaGPT config example

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

## Smoke test

```bash
TMP_HOME=$(mktemp -d)
mkdir -p "$TMP_HOME/.metagpt"
cat > "$TMP_HOME/.metagpt/config2.yaml" <<'YAML'
llm:
  api_type: "openai_codex"
  auth_mode: "oauth_profile"
  auth_profile: "openai-codex:default"
  base_url: "http://127.0.0.1:8787/v1"
  api_key: ""
  model: "gpt-5.4"
  stream: true
YAML

HOME="$TMP_HOME" ./scripts/metagpt-local.sh auth import-openclaw-codex default \
  /home/khj12/.openclaw/agents/main/agent/auth-profiles.json \
  openai-codex:default

HOME="$TMP_HOME" ./.venv/bin/metagpt startup "Create a hello world script" \
  --n-round 1 \
  --code-review false \
  --run-tests false \
  --implement false \
  --project-name codex_bridge_smoke
```

## Notes

- Current bridge uses **OpenClaw-managed access tokens read-only**.
- It does **not** refresh tokens itself in v1.
- If the incoming bearer token is missing or not a ChatGPT JWT, the bridge falls back to the OpenClaw auth store.
- Tool calls are implemented by asking Codex to return JSON matching the requested function schema.
