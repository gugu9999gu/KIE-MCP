# KIE-MCP

A production-oriented Model Context Protocol (MCP) server for the [KIE AI API](https://docs.kie.ai/).

This repository wraps KIE's unified asynchronous generation API, account utilities, temporary file uploads, chat-compatible endpoints, webhook verification, and the complete English documentation index in MCP tools and resources.

## What was analyzed

The implementation is based on KIE's official documentation index and endpoint pages. The current English documentation index bundled in this repository contains **208 pages** across:

- Market image, video, audio, and chat models
- 4o Image, Flux Kontext, Runway, Suno, and Veo 3.1 API families
- Common account and download APIs
- URL, Base64, and multipart file uploads
- Callback payloads and webhook HMAC verification

KIE has two main API surfaces:

| Surface | Base URL | Purpose |
|---|---|---|
| Main API | `https://api.kie.ai` | Generation jobs, chat endpoints, credits, download URLs, legacy/specialized APIs |
| Upload API | `https://kieai.redpandaai.co` | URL, Base64, and multipart temporary file uploads |

### Unified Market task flow

Most Market models use the same asynchronous contract:

1. `POST /api/v1/jobs/createTask`
2. Receive `data.taskId`
3. Use a production `callBackUrl`, or poll `GET /api/v1/jobs/recordInfo?taskId=...`
4. Handle `waiting`, `queuing`, `generating`, `success`, and `fail`
5. Persist result files promptly

Create-task body:

```json
{
  "model": "nano-banana-2",
  "callBackUrl": "https://example.com/kie/callback",
  "input": {
    "prompt": "A cinematic city at sunrise",
    "aspect_ratio": "16:9"
  }
}
```

The exact `input` schema is model-specific. Use `kie_search_docs` followed by `kie_get_documentation` before invoking an unfamiliar model.

### Operational constraints found in the official docs

- Authentication uses `Authorization: Bearer <API key>`.
- KIE documents a default generation submission limit of 20 new tasks per 10 seconds per account.
- A `200 OK` task-creation response confirms queue admission, not completion.
- Production integrations should use callbacks rather than aggressive polling.
- The common download endpoint returns links valid for 20 minutes.
- KIE documentation contains conflicting retention statements for uploaded/generated files (24 hours, 3 days, and 14 days appear on different pages). This server deliberately treats every hosted result as temporary and recommends immediate persistence.
- Webhook signatures use Base64-encoded HMAC-SHA256 over `taskId + "." + timestamp`.

## MCP tools

### Documentation

| Tool | Description |
|---|---|
| `kie_search_docs` | Search the bundled 208-page English documentation index |
| `kie_get_documentation` | Fetch official Markdown from `docs.kie.ai` by slug or URL |

### Unified generation

| Tool | Description |
|---|---|
| `kie_create_task` | Create a task for any unified Market model |
| `kie_get_task` | Query task state and results |
| `kie_wait_for_task` | Poll with bounded exponential backoff |
| `kie_api_request` | GET/POST fallback for documented specialized KIE endpoints |

`kie_get_task` additionally decodes the API's JSON-encoded `param` and `resultJson` fields into `paramParsed` and `resultParsed`.

### Chat APIs

| Tool | Description |
|---|---|
| `kie_chat_completions` | OpenAI-compatible chat-completions endpoints, including model-specific Gemini paths |
| `kie_claude_messages` | `/claude/v1/messages` |
| `kie_responses` | `/codex/v1/responses` |

Streaming responses are collected from SSE into a structured `events` array so MCP clients receive one completed tool result.

### Account and files

| Tool | Description |
|---|---|
| `kie_get_credits` | `GET /api/v1/chat/credit` |
| `kie_get_download_url` | Create a 20-minute KIE download URL |
| `kie_upload_from_url` | Upload a public remote file |
| `kie_upload_base64` | Upload a small Base64 payload or data URL |
| `kie_upload_local_file` | Multipart upload from an explicitly allowed local directory |

Local file access is disabled by default. Set `KIE_ALLOWED_UPLOAD_ROOT` to a specific directory to enable it. Resolved paths must remain inside that root and files are checked against `KIE_MAX_UPLOAD_BYTES`.

### Security

| Tool | Description |
|---|---|
| `kie_verify_webhook` | Verify KIE callback signatures and reject stale timestamps |

The generic request tool accepts only relative paths and only `GET` or `POST`, so it cannot redirect credentials to an arbitrary host.

## MCP resources

- `kie://docs/overview` — analyzed API architecture and constraints
- `kie://docs/catalog` — complete bundled documentation index
- `kie://docs/entry/{slug}` — metadata for one documentation page

## Installation

Python 3.11 or newer is required. The project pins the stable MCP Python SDK v1 line because v2 remains pre-release as of June 2026.

Using `uv`:

```bash
uv sync
cp .env.example .env
```

Using pip:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Set the key in the process environment:

```bash
export KIE_API_KEY="your-kie-api-key"
```

Run over stdio:

```bash
kie-mcp
```

Run the source directly:

```bash
python -m kie_mcp.server
```

## Client configuration

### Claude Desktop / compatible stdio clients

```json
{
  "mcpServers": {
    "kie": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/KIE-MCP",
        "run",
        "kie-mcp"
      ],
      "env": {
        "KIE_API_KEY": "your-kie-api-key"
      }
    }
  }
}
```

Do not commit the real key. Prefer the host application's secret or environment management.

### Streamable HTTP

```bash
export KIE_API_KEY="your-kie-api-key"
export KIE_MCP_TRANSPORT="streamable-http"
kie-mcp
```

For an internet-facing deployment, place the server behind TLS, authentication, request-size limits, and network-level access controls. The KIE key authorizes paid operations and must not be exposed to untrusted MCP clients.

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `KIE_API_KEY` | none | Required for KIE network operations |
| `KIE_API_BASE` | `https://api.kie.ai` | Main API base |
| `KIE_UPLOAD_BASE` | `https://kieai.redpandaai.co` | Upload API base |
| `KIE_TIMEOUT_SECONDS` | `120` | Per-request timeout |
| `KIE_MAX_RETRIES` | `3` | Retries for network failures, HTTP 429, and 5xx |
| `KIE_MAX_UPLOAD_BYTES` | `104857600` | Maximum local multipart file size |
| `KIE_ALLOWED_UPLOAD_ROOT` | unset | Required root for local uploads |
| `KIE_MCP_TRANSPORT` | `stdio` | `stdio`, `sse`, or `streamable-http` |

## Example agent workflow

1. Search for a model:

```text
kie_search_docs(query="nano banana 2")
```

2. Read its exact schema:

```text
kie_get_documentation(slug_or_url="market--google--nanobanana2")
```

3. Create the task:

```text
kie_create_task(
  model="nano-banana-2",
  input={
    "prompt": "Minimal product photograph on a white seamless background",
    "image_input": [],
    "aspect_ratio": "1:1",
    "resolution": "1K",
    "output_format": "png"
  }
)
```

4. Wait for completion:

```text
kie_wait_for_task(task_id="...")
```

5. Persist returned media immediately. Use `kie_get_download_url` when a direct-download URL is required.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

Tests cover task response normalization, catalog search, path restrictions, and webhook signature/replay verification.

## Design decisions

### One generic Market tool instead of hundreds of model tools

KIE's Market models share one task endpoint while model parameters change frequently. Generating a separate MCP tool for every model would make tool discovery noisy and cause the server to become stale whenever KIE adds or revises a model. The server therefore exposes:

- a stable generic task tool,
- an official documentation search/fetch layer,
- dedicated wrappers only for materially different API protocols.

### API responses are preserved

KIE response bodies are returned without lossy remapping. This protects compatibility with newly added fields and model-specific result shapes. Only stringified JSON fields in task records receive additional parsed copies.

### Bounded retries

Only network failures, HTTP 429, and 5xx are retried. Client and validation errors are returned immediately. Polling is capped at 15 minutes by the MCP tool to avoid unbounded agent calls.

## License

MIT
