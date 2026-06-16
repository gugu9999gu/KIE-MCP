from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .catalog import CATALOG, get_catalog_entry, search_catalog
from .client import KieClient, fetch_documentation
from .config import Settings
from .errors import KieAPIError
from .webhook import verify_webhook_signature

settings = Settings.from_env()
client = KieClient(settings)

mcp = FastMCP(
    "KIE API",
    instructions=(
        "Use KIE AI generation, chat, file upload, account, and documentation tools. "
        "Generation tasks are asynchronous: create a task, then query or wait for it."
    ),
    json_response=True,
)


def _result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"data": value}


async def _safe_call(coro: Any) -> dict[str, Any]:
    try:
        return _result(await coro)
    except KieAPIError as exc:
        return exc.as_dict()
    except Exception as exc:
        return {"error": str(exc), "type": type(exc).__name__}


@mcp.resource("kie://docs/overview")
def docs_overview() -> str:
    """Return the analyzed KIE API architecture and operational constraints."""
    return json.dumps(
        {
            "authentication": "Authorization: Bearer $KIE_API_KEY",
            "api_base": settings.api_base,
            "upload_base": settings.upload_base,
            "market": {
                "create": "POST /api/v1/jobs/createTask",
                "query": "GET /api/v1/jobs/recordInfo?taskId=...",
                "states": ["waiting", "queuing", "generating", "success", "fail"],
                "recommended": "Use callBackUrl in production; poll with backoff otherwise.",
            },
            "common": {
                "credits": "GET /api/v1/chat/credit",
                "download_url": "POST /api/v1/common/download-url",
                "download_url_ttl": "20 minutes",
            },
            "uploads": {
                "url": "POST /api/file-url-upload",
                "base64": "POST /api/file-base64-upload",
                "stream": "POST /api/file-stream-upload",
                "warning": "KIE documentation contains inconsistent retention durations; "
                "treat uploaded/generated URLs as temporary and persist results promptly.",
            },
            "rate_limit": "Default documentation states 20 new generation requests per 10 seconds.",
            "documentation_entries": len(CATALOG),
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.resource("kie://docs/catalog")
def docs_catalog() -> str:
    """Return the bundled index of English KIE documentation pages."""
    return json.dumps(CATALOG, ensure_ascii=False, indent=2)


@mcp.resource("kie://docs/entry/{slug}")
def docs_entry(slug: str) -> str:
    """Return metadata for one KIE documentation entry."""
    entry = get_catalog_entry(slug)
    return json.dumps(
        entry or {"error": f"unknown documentation slug: {slug}"},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def kie_search_docs(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search all bundled English KIE API documentation titles, paths, and categories."""
    return search_catalog(query, limit)


@mcp.tool()
async def kie_get_documentation(slug_or_url: str, max_characters: int = 60_000) -> dict[str, Any]:
    """Fetch the official Markdown for a documentation slug or docs.kie.ai .md URL."""
    entry = get_catalog_entry(slug_or_url)
    url = entry["url"] if entry else slug_or_url
    try:
        text = await fetch_documentation(url)
        limit = max(1_000, min(max_characters, 200_000))
        return {
            "url": url,
            "truncated": len(text) > limit,
            "content": text[:limit],
        }
    except Exception as exc:
        return {"error": str(exc), "url": url}


@mcp.tool()
async def kie_create_task(
    model: str,
    input: dict[str, Any],
    callback_url: str | None = None,
) -> dict[str, Any]:
    """Create an asynchronous Market task for any KIE image, video, or audio model."""
    return await _safe_call(client.create_task(model, input, callback_url))


@mcp.tool()
async def kie_get_task(
    task_id: str,
    parse_json_fields: bool = True,
) -> dict[str, Any]:
    """Get status and results for a Market task."""
    return await _safe_call(
        client.get_task(task_id, parse_json_fields=parse_json_fields)
    )


@mcp.tool()
async def kie_wait_for_task(
    task_id: str,
    timeout_seconds: int = 900,
    initial_interval_seconds: float = 2.0,
    max_interval_seconds: float = 15.0,
) -> dict[str, Any]:
    """Poll a Market task with exponential backoff until success, failure, or timeout."""
    timeout_seconds = max(1, min(timeout_seconds, 900))
    interval = max(0.5, min(initial_interval_seconds, 15.0))
    max_interval_seconds = max(interval, min(max_interval_seconds, 30.0))
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict[str, Any] | None = None

    while asyncio.get_running_loop().time() < deadline:
        result = await _safe_call(client.get_task(task_id, parse_json_fields=True))
        last = result
        if "error" in result:
            return result
        data = result.get("data")
        state = data.get("state") if isinstance(data, dict) else None
        if state in {"success", "fail"}:
            return result
        await asyncio.sleep(interval)
        interval = min(interval * 1.6, max_interval_seconds)

    return {
        "error": "timed out while waiting for KIE task",
        "task_id": task_id,
        "timeout_seconds": timeout_seconds,
        "last_response": last,
        "retryable": True,
    }


@mcp.tool()
async def kie_get_credits() -> dict[str, Any]:
    """Get the current KIE account credit balance."""
    return await _safe_call(client.get_credits())


@mcp.tool()
async def kie_get_download_url(url: str) -> dict[str, Any]:
    """Create a temporary direct-download URL for a KIE-generated file."""
    return await _safe_call(client.get_download_url(url))


@mcp.tool()
async def kie_upload_from_url(
    file_url: str,
    upload_path: str = "mcp/url",
    file_name: str | None = None,
) -> dict[str, Any]:
    """Ask KIE to download and temporarily host a public HTTP(S) file."""
    return await _safe_call(client.upload_from_url(file_url, upload_path, file_name))


@mcp.tool()
async def kie_upload_base64(
    base64_data: str,
    upload_path: str = "mcp/base64",
    file_name: str | None = None,
) -> dict[str, Any]:
    """Upload a small Base64 string or data URL to KIE temporary storage."""
    return await _safe_call(client.upload_base64(base64_data, upload_path, file_name))


@mcp.tool()
async def kie_upload_local_file(
    file_path: str,
    upload_path: str = "mcp/files",
    file_name: str | None = None,
) -> dict[str, Any]:
    """Upload a local file inside KIE_ALLOWED_UPLOAD_ROOT as multipart/form-data."""
    return await _safe_call(client.upload_local_file(file_path, upload_path, file_name))


@mcp.tool()
async def kie_chat_completions(
    endpoint: str,
    messages: list[dict[str, Any]],
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    """Call a KIE OpenAI-compatible chat-completions endpoint."""
    body: dict[str, Any] = {"messages": messages, "stream": stream}
    if model:
        body["model"] = model
    if tools is not None:
        body["tools"] = tools
    if extra:
        body.update(extra)
    return await _safe_call(client.request("POST", endpoint, json_body=body))


@mcp.tool()
async def kie_claude_messages(
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 4096,
    tools: list[dict[str, Any]] | None = None,
    thinking_flag: bool | None = None,
    extra: dict[str, Any] | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    """Call KIE's Anthropic-compatible /claude/v1/messages endpoint."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if tools is not None:
        body["tools"] = tools
    if thinking_flag is not None:
        body["thinkingFlag"] = thinking_flag
    if extra:
        body.update(extra)
    return await _safe_call(
        client.request("POST", "/claude/v1/messages", json_body=body)
    )


@mcp.tool()
async def kie_responses(
    model: str,
    input: list[dict[str, Any]] | str,
    tools: list[dict[str, Any]] | None = None,
    reasoning: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    """Call KIE's OpenAI Responses-compatible /codex/v1/responses endpoint."""
    body: dict[str, Any] = {"model": model, "input": input, "stream": stream}
    if tools is not None:
        body["tools"] = tools
    if reasoning is not None:
        body["reasoning"] = reasoning
    if extra:
        body.update(extra)
    return await _safe_call(
        client.request("POST", "/codex/v1/responses", json_body=body)
    )


@mcp.tool()
async def kie_api_request(
    method: str,
    path: str,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    base: str = "api",
) -> dict[str, Any]:
    """Advanced fallback for documented KIE GET/POST endpoints using a relative path."""
    if base not in {"api", "upload"}:
        return {"error": "base must be 'api' or 'upload'"}
    return await _safe_call(
        client.request(
            method,
            path,
            base=base,  # type: ignore[arg-type]
            params=query,
            json_body=body,
        )
    )


@mcp.tool()
def kie_verify_webhook(
    body: dict[str, Any],
    timestamp: str,
    signature: str,
    secret: str,
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    """Verify a KIE callback using Base64(HMAC-SHA256(taskId.timestamp, secret))."""
    return verify_webhook_signature(
        body=body,
        timestamp=timestamp,
        signature=signature,
        secret=secret,
        max_age_seconds=max_age_seconds,
    )


def main() -> None:
    if settings.transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError(
            "KIE_MCP_TRANSPORT must be one of: stdio, sse, streamable-http"
        )
    mcp.run(transport=settings.transport)


if __name__ == "__main__":
    main()
