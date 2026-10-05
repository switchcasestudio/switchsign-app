# OpenClaw → SwitchSign integration

This folder contains the SwitchSign integration artifacts for an OpenClaw agent.

## Files

- `switchsign-tool.json` — OpenAI-style function schema for creating contracts
- `switchsign_tool.py` — small Python/httpx wrapper for OpenClaw runtimes that can call Python functions
- `agent-system-prompt.md` — system prompt/instructions for contract drafting behavior

## Setup

1. Add the SwitchSign tool to OpenClaw using the format your OpenClaw runtime supports:
   - use `switchsign-tool.json` for OpenAI-style function schemas
   - use `switchsign_tool.py` if the runtime supports Python function wrappers

2. Configure these environment variables in OpenClaw, not in chat:

   ```env
   SWITCHSIGN_BASE_URL=https://contracts.example.com
   SWITCHSIGN_API_KEY=<key from /opt/switchsign/.env>
   ```

3. Paste or import `agent-system-prompt.md` into the target agent's system prompt/instructions.

4. Restart OpenClaw if the tool/environment configuration requires it.

## Connectivity test

From the VPS:

```bash
cd /opt/switchsign
source .env
curl -s -H "X-API-Key: $SWITCHSIGN_API_KEY" \
  "$SWITCHSIGN_BASE_URL/api/agent/ping"
```

Expected:

```json
{"ok":true,"service":"switchsign","version":"0.1.0"}
```

## Conversational test

Ask the configured OpenClaw agent:

> Create a test agreement for Test Customer (testcustomer@example.com) for a $300 logo design package, 3-day delivery, standard terms.

Expected:

- The agent asks for anything material that is missing, or proceeds if enough information is present.
- The agent calls `switchsign_create_contract` once.
- The agent replies with a SwitchSign signing URL and summary.

## Troubleshooting

- `401` / `403`: `SWITCHSIGN_API_KEY` is missing or wrong.
- Connection refused / timeout: SwitchSign container may be down; check `docker compose logs switchsign`.
- Agent does not call tool: confirm the tool is registered and the system prompt is loaded.
- Agreement text is too vague: improve the prompt or require missing scope/pricing/timeline before tool calls.
