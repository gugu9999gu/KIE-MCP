from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None else float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str | None
    api_base: str
    upload_base: str
    timeout_seconds: float
    max_retries: int
    max_upload_bytes: int
    allowed_upload_root: Path | None
    transport: str

    @classmethod
    def from_env(cls) -> Settings:
        allowed_root = os.getenv("KIE_ALLOWED_UPLOAD_ROOT")
        return cls(
            api_key=os.getenv("KIE_API_KEY"),
            api_base=os.getenv("KIE_API_BASE", "https://api.kie.ai").rstrip("/"),
            upload_base=os.getenv(
                "KIE_UPLOAD_BASE", "https://kieai.redpandaai.co"
            ).rstrip("/"),
            timeout_seconds=_env_float("KIE_TIMEOUT_SECONDS", 120.0),
            max_retries=_env_int("KIE_MAX_RETRIES", 3),
            max_upload_bytes=_env_int("KIE_MAX_UPLOAD_BYTES", 100 * 1024 * 1024),
            allowed_upload_root=Path(allowed_root).expanduser().resolve()
            if allowed_root
            else None,
            transport=os.getenv("KIE_MCP_TRANSPORT", "stdio"),
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "KIE_API_KEY is not configured. Set it in the MCP server environment."
            )
        return self.api_key
