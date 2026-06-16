from __future__ import annotations

import asyncio
import json
import mimetypes
import random
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from .config import Settings
from .errors import KieAPIError

BaseName = Literal["api", "upload"]


def _parse_json_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


class KieClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _base_url(self, base: BaseName) -> str:
        return self.settings.api_base if base == "api" else self.settings.upload_base

    def _headers(self, *, content_type: str | None = "application/json") -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.settings.require_api_key()}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _validate_relative_path(path: str) -> str:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("path must be an absolute API path beginning with one '/'")
        parsed = urlparse(path)
        if parsed.scheme or parsed.netloc:
            raise ValueError("full URLs are not accepted; pass a relative KIE API path")
        return path

    @staticmethod
    def _decode_response(response: httpx.Response) -> Any:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            events: list[Any] = []
            for line in response.text.splitlines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    events.append(json.loads(data))
                except json.JSONDecodeError:
                    events.append(data)
            return {"stream": True, "events": events}

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return {"text": response.text, "content_type": content_type}

    async def request(
        self,
        method: str,
        path: str,
        *,
        base: BaseName = "api",
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        path = self._validate_relative_path(path)
        method = method.upper()
        if method not in {"GET", "POST"}:
            raise ValueError("only GET and POST are allowed")

        headers = self._headers(content_type=None if files else "application/json")
        url = f"{self._base_url(base)}{path}"
        last_error: Exception | None = None

        for attempt in range(self.settings.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    files=files,
                    data=data,
                )
                payload = self._decode_response(response)
                if response.is_success:
                    return payload

                retryable = response.status_code == 429 or response.status_code >= 500
                error = KieAPIError(
                    message=self._message_from_payload(payload, response.reason_phrase),
                    status_code=response.status_code,
                    api_code=payload.get("code") if isinstance(payload, dict) else None,
                    details=payload,
                    retryable=retryable,
                )
                if not retryable or attempt >= self.settings.max_retries:
                    raise error
                last_error = error
                await asyncio.sleep(self._retry_delay(response, attempt))
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt >= self.settings.max_retries:
                    raise KieAPIError(
                        message="KIE API network request failed",
                        details=str(exc),
                        retryable=True,
                    ) from exc
                await asyncio.sleep(min(2**attempt + random.random(), 10.0))

        raise KieAPIError(message="KIE API request failed", details=str(last_error))

    @staticmethod
    def _message_from_payload(payload: Any, fallback: str) -> str:
        if isinstance(payload, dict):
            for key in ("msg", "message", "error"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
        return fallback or "KIE API request failed"

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
        return min(2**attempt + random.random(), 10.0)

    async def create_task(
        self, model: str, input_data: dict[str, Any], callback_url: str | None = None
    ) -> Any:
        body: dict[str, Any] = {"model": model, "input": input_data}
        if callback_url:
            body["callBackUrl"] = callback_url
        return await self.request("POST", "/api/v1/jobs/createTask", json_body=body)

    async def get_task(self, task_id: str, *, parse_json_fields: bool = True) -> Any:
        payload = await self.request(
            "GET", "/api/v1/jobs/recordInfo", params={"taskId": task_id}
        )
        if parse_json_fields and isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                if "param" in data:
                    data["paramParsed"] = _parse_json_string(data["param"])
                if "resultJson" in data:
                    data["resultParsed"] = _parse_json_string(data["resultJson"])
        return payload

    async def get_credits(self) -> Any:
        return await self.request("GET", "/api/v1/chat/credit")

    async def get_download_url(self, url: str) -> Any:
        return await self.request(
            "POST", "/api/v1/common/download-url", json_body={"url": url}
        )

    async def upload_from_url(
        self, file_url: str, upload_path: str, file_name: str | None
    ) -> Any:
        body: dict[str, Any] = {"fileUrl": file_url, "uploadPath": upload_path}
        if file_name:
            body["fileName"] = file_name
        return await self.request(
            "POST", "/api/file-url-upload", base="upload", json_body=body
        )

    async def upload_base64(
        self, base64_data: str, upload_path: str, file_name: str | None
    ) -> Any:
        body: dict[str, Any] = {"base64Data": base64_data, "uploadPath": upload_path}
        if file_name:
            body["fileName"] = file_name
        return await self.request(
            "POST", "/api/file-base64-upload", base="upload", json_body=body
        )

    def validate_local_upload(self, file_path: str) -> Path:
        root = self.settings.allowed_upload_root
        if root is None:
            raise ValueError(
                "local file upload is disabled. Set KIE_ALLOWED_UPLOAD_ROOT to an "
                "explicit directory."
            )
        path = Path(file_path).expanduser().resolve(strict=True)
        if path != root and root not in path.parents:
            raise ValueError("file is outside KIE_ALLOWED_UPLOAD_ROOT")
        if not path.is_file():
            raise ValueError("file_path is not a regular file")
        size = path.stat().st_size
        if size > self.settings.max_upload_bytes:
            raise ValueError(
                f"file exceeds KIE_MAX_UPLOAD_BYTES ({self.settings.max_upload_bytes})"
            )
        return path

    async def upload_local_file(
        self, file_path: str, upload_path: str, file_name: str | None
    ) -> Any:
        path = self.validate_local_upload(file_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            files = {"file": (path.name, handle, mime_type)}
            data = {"uploadPath": upload_path}
            if file_name:
                data["fileName"] = file_name
            return await self.request(
                "POST",
                "/api/file-stream-upload",
                base="upload",
                files=files,
                data=data,
            )


async def fetch_documentation(url: str, timeout_seconds: float = 30.0) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "docs.kie.ai":
        raise ValueError("documentation URL must use https://docs.kie.ai")
    if not parsed.path.endswith(".md"):
        raise ValueError("documentation URL must point to a .md document")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds), follow_redirects=False
    ) as client:
        response = await client.get(url, headers={"Accept": "text/markdown,text/plain"})
        response.raise_for_status()
        return response.text
