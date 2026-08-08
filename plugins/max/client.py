"""Minimal MAX Bot API v2 HTTP client.

The client deliberately uses the documented REST surface instead of the
older third-party Python SDK.  This keeps the Hermes plugin independent from
SDK release timing and makes authorization/TLS behavior testable.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional

import httpx

DEFAULT_API_BASE = "https://platform-api2.max.ru"
DEFAULT_TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=60.0, pool=15.0)
_SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{5,256}$")


class MaxApiError(RuntimeError):
    """An API or transport error with machine-readable retry information."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        retry_after: Optional[float] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after
        self.retryable = retryable


def _retry_after(response: httpx.Response) -> Optional[float]:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MaxClient:
    """Async client for the current MAX Bot API v2 endpoint."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_API_BASE,
        timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
        verify: bool | str = True,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        token = token.strip()
        if not token:
            raise ValueError("MAX bot token must not be empty")
        self.base_url = base_url.rstrip("/")
        self._http = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": token},
            timeout=timeout,
            verify=verify,
        )
        # A supplied client is primarily a test/integration seam.  Its
        # existing base URL and transport remain intact, but auth is owned by
        # this client so callers cannot accidentally omit the header.
        self._http.headers["Authorization"] = token

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Optional[Mapping[str, Any]] = None,
        files: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        try:
            response = await self._http.request(
                method,
                path,
                params=params,
                json=json,
                files=files,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise MaxApiError(
                f"MAX transport error: {exc.__class__.__name__}",
                retryable=True,
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.status_code >= 400:
            error = payload if isinstance(payload, Mapping) else {}
            code = error.get("code")
            detail = error.get("message") or response.reason_phrase or "MAX API error"
            retryable = response.status_code == 429 or response.status_code >= 500
            raise MaxApiError(
                f"MAX API {response.status_code}: {detail}",
                status_code=response.status_code,
                code=str(code) if code else None,
                retry_after=_retry_after(response),
                retryable=retryable,
            )

        return payload if payload is not None else {}

    async def get_me(self) -> Mapping[str, Any]:
        return await self._request("GET", "/me")

    async def send_message(
        self,
        target_id: str,
        text: str,
        *,
        target_type: str = "user",
        link: Optional[Mapping[str, Any]] = None,
        attachments: Optional[Iterable[Mapping[str, Any]]] = None,
        notify: bool = True,
        format: str = "markdown",
    ) -> Mapping[str, Any]:
        if len(text) > 4000:
            raise ValueError("MAX message text must not exceed 4000 characters")
        if target_type not in {"user", "chat"}:
            raise ValueError("target_type must be 'user' or 'chat'")
        body: dict[str, Any] = {"text": text, "notify": notify, "format": format}
        if link is not None:
            body["link"] = dict(link)
        if attachments is not None:
            body["attachments"] = [dict(item) for item in attachments]
        return await self._request(
            "POST",
            "/messages",
            params={"user_id" if target_type == "user" else "chat_id": target_id},
            json=body,
        )

    async def edit_message(
        self,
        message_id: str,
        text: str,
        *,
        attachments: Optional[Iterable[Mapping[str, Any]]] = None,
        notify: bool = True,
        format: str = "markdown",
    ) -> Mapping[str, Any]:
        if len(text) > 4000:
            raise ValueError("MAX message text must not exceed 4000 characters")
        body: dict[str, Any] = {"text": text, "notify": notify, "format": format}
        if attachments is not None:
            body["attachments"] = [dict(item) for item in attachments]
        return await self._request(
            "PUT",
            "/messages",
            params={"message_id": message_id},
            json=body,
        )

    async def subscribe_webhook(
        self,
        url: str,
        secret: str,
        *,
        update_types: Optional[Iterable[str]] = None,
    ) -> Mapping[str, Any]:
        if not url.startswith("https://"):
            raise ValueError("MAX Webhook URL must use HTTPS")
        if not _SECRET_PATTERN.fullmatch(secret):
            raise ValueError("MAX Webhook secret must match [A-Za-z0-9_-]{5,256}")
        body: dict[str, Any] = {"url": url, "secret": secret}
        if update_types is not None:
            body["update_types"] = list(update_types)
        return await self._request("POST", "/subscriptions", json=body)

    async def get_subscriptions(self) -> Any:
        return await self._request("GET", "/subscriptions")

    async def get_updates(
        self,
        *,
        marker: Optional[int] = None,
        timeout: int = 30,
        limit: int = 100,
        types: Optional[Iterable[str]] = None,
    ) -> Mapping[str, Any]:
        if not 0 <= timeout <= 90:
            raise ValueError("MAX polling timeout must be between 0 and 90 seconds")
        if not 1 <= limit <= 1000:
            raise ValueError("MAX polling limit must be between 1 and 1000")
        params: dict[str, Any] = {"timeout": timeout, "limit": limit}
        if marker is not None:
            params["marker"] = marker
        if types is not None:
            params["types"] = ",".join(types)
        return await self._request("GET", "/updates", params=params)
