from __future__ import annotations

import pytest
import httpx
from types import SimpleNamespace

import plugins.max.adapter as max_adapter_module
from plugins.max.adapter import _build_message_event
from plugins.max.adapter import standalone_send
from plugins.max.client import MaxClient
from plugins.max.models import MaxMessage
from plugins.max.media import (
    MaxAttachment,
    attachment_from_payload,
    is_allowed_media_url,
    media_type_for_file,
    mime_type_for_file,
)


def test_attachment_from_payload_extracts_nested_url_and_filename() -> None:
    attachment = attachment_from_payload(
        {
            "type": "image",
            "payload": {
                "url": "https://iu.oneme.ru/attachments/photo-123.png?sig=secret",
                "filename": "screen.png",
                "mime_type": "image/png",
            },
        }
    )

    assert attachment == MaxAttachment(
        kind="image",
        url="https://iu.oneme.ru/attachments/photo-123.png?sig=secret",
        filename="screen.png",
        mime_type="image/png",
    )


def test_attachment_from_payload_normalizes_voice_to_audio() -> None:
    attachment = attachment_from_payload(
        {
            "type": "voice",
            "payload": {"url": "https://vu.okcdn.ru/voice.ogg"},
        }
    )

    assert attachment is not None
    assert attachment.kind == "audio"
    assert attachment.filename == "voice.ogg"


def test_attachment_from_payload_keeps_nested_token_without_url() -> None:
    attachment = attachment_from_payload(
        {
            "type": "video",
            "payload": {"token": "video-token", "filename": "clip.mp4"},
        }
    )

    assert attachment is not None
    assert attachment.url is None
    assert attachment.token == "video-token"
    assert attachment.filename == "clip.mp4"


def test_attachment_from_payload_uses_safe_url_basename() -> None:
    attachment = attachment_from_payload(
        {
            "type": "file",
            "url": "https://fu.oneme.ru/a/../report.pdf?token=hidden",
        }
    )

    assert attachment is not None
    assert attachment.filename == "report.pdf"
    assert attachment.mime_type == "application/octet-stream"


def test_attachment_from_payload_ignores_unsupported_types() -> None:
    assert attachment_from_payload({"type": "sticker", "payload": {}}) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://iu.oneme.ru/file.png",
        "https://fu.oneme.ru/file.pdf",
        "https://vu.okcdn.ru/file.mp4",
        "https://cdn.max.ru/file.bin",
    ],
)
def test_official_media_hosts_are_allowed(url: str) -> None:
    assert is_allowed_media_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://iu.oneme.ru/file.png",
        "https://127.0.0.1/file.png",
        "https://iu.oneme.ru.evil.example/file.png",
        "https://evil.example/file.png",
        "not-a-url",
    ],
)
def test_media_url_guard_rejects_non_official_or_unsafe_hosts(url: str) -> None:
    assert is_allowed_media_url(url) is False


@pytest.mark.parametrize(
    ("path", "expected"),
    [("photo.png", "image"), ("clip.mp4", "video"), ("voice.ogg", "audio"), ("report.pdf", "file")],
)
def test_media_type_for_file(path: str, expected: str) -> None:
    assert media_type_for_file(path) == expected


def test_media_type_for_file_supports_voice_and_force_document() -> None:
    assert media_type_for_file("voice.bin", is_voice=True) == "audio"
    assert media_type_for_file("photo.png", force_document=True) == "file"


def test_mime_type_for_file_uses_extension_then_safe_fallback() -> None:
    assert mime_type_for_file("photo.png", "image") == "image/png"
    assert mime_type_for_file("unknown.max", "file") == "application/octet-stream"


@pytest.mark.asyncio
async def test_client_download_media_does_not_send_bot_token_to_cdn() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "image/png", "content-length": "8"},
            content=b"\x89PNG\r\n\x1a\n",
            request=request,
        )

    api_http = httpx.AsyncClient(
        base_url="https://platform-api2.max.ru",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}, request=request)),
    )
    media_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MaxClient(
        "secret-token",
        http_client=api_http,
        media_http_client=media_http,
    )

    data, mime_type = await client.download_media("https://iu.oneme.ru/file.png", max_bytes=16)
    await client.close()

    assert data.startswith(b"\x89PNG")
    assert mime_type == "image/png"
    assert seen
    assert "authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_client_upload_media_uses_current_upload_contract(tmp_path) -> None:
    uploaded = []

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/uploads"
        assert request.url.params["type"] == "file"
        return httpx.Response(
            200,
            json={"url": "https://fu.oneme.ru/upload.do?sig=opaque"},
            request=request,
        )

    def media_handler(request: httpx.Request) -> httpx.Response:
        uploaded.append(request)
        return httpx.Response(200, json={"token": "uploaded-token"}, request=request)

    path = tmp_path / "report.txt"
    path.write_text("hello", encoding="utf-8")
    api_http = httpx.AsyncClient(
        base_url="https://platform-api2.max.ru",
        transport=httpx.MockTransport(api_handler),
    )
    media_http = httpx.AsyncClient(transport=httpx.MockTransport(media_handler))
    client = MaxClient(
        "secret-token",
        http_client=api_http,
        media_http_client=media_http,
    )

    result = await client.upload_media(path, media_type="file", max_bytes=16)
    await client.close()

    assert result["token"] == "uploaded-token"
    assert uploaded
    assert b"report.txt" in uploaded[0].content
    assert b"hello" in uploaded[0].content
    assert "authorization" not in uploaded[0].headers


@pytest.mark.asyncio
async def test_adapter_caches_incoming_media_into_hermes_event(monkeypatch) -> None:
    class _Client:
        async def download_media(self, url: str, *, max_bytes: int):
            assert url == "https://iu.oneme.ru/photo.png"
            assert max_bytes == 1024
            return b"image-bytes", "image/png"

    cached = type(
        "Cached",
        (),
        {
            "path": "/home/xidden/.hermes/cache/images/max.png",
            "media_type": "image/png",
            "context_note": lambda self: "[image 'photo.png' saved at: /home/xidden/.hermes/cache/images/max.png]",
        },
    )()
    monkeypatch.setattr(max_adapter_module, "_cache_media_bytes", lambda *args, **kwargs: cached)

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._media_max_bytes = 1024
    adapter.platform = "max"
    message = MaxMessage(
        message_id="mid-1",
        user_id="user-1",
        user_name="User",
        chat_id="user-1",
        chat_type="dialog",
        chat_title=None,
        text="Посмотри",
        attachments=(
            {
                "type": "image",
                "payload": {"url": "https://iu.oneme.ru/photo.png", "filename": "photo.png"},
            },
        ),
    )
    event = _build_message_event(adapter, message)

    await adapter._populate_message_media(message, event)

    assert event.media_urls == ["/home/xidden/.hermes/cache/images/max.png"]
    assert event.media_types == ["image/png"]
    assert "saved at" in event.text


@pytest.mark.asyncio
async def test_adapter_resolves_inbound_video_token_before_download(monkeypatch) -> None:
    class _Client:
        async def get_video(self, token: str):
            assert token == "video-token"
            return {"urls": {"mp4": "https://vu.okcdn.ru/video.mp4"}}

        async def download_media(self, url: str, *, max_bytes: int):
            assert url == "https://vu.okcdn.ru/video.mp4"
            return b"video-bytes", "video/mp4"

    cached = type(
        "Cached",
        (),
        {
            "path": "/home/xidden/.hermes/cache/videos/max.mp4",
            "media_type": "video/mp4",
            "context_note": lambda self: "[video saved at: /home/xidden/.hermes/cache/videos/max.mp4]",
        },
    )()
    monkeypatch.setattr(max_adapter_module, "_cache_media_bytes", lambda *args, **kwargs: cached)

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter.platform = "max"
    adapter._media_max_bytes = 1024
    message = MaxMessage(
        message_id="mid-video",
        user_id="user-1",
        user_name="User",
        chat_id="user-1",
        chat_type="dialog",
        chat_title=None,
        text="Видео",
        attachments=({"type": "video", "payload": {"token": "video-token"}},),
    )
    event = _build_message_event(adapter, message)

    await adapter._populate_message_media(message, event)

    assert event.media_urls == ["/home/xidden/.hermes/cache/videos/max.mp4"]
    assert event.media_types == ["video/mp4"]


@pytest.mark.asyncio
async def test_adapter_sends_media_tag_as_token_attachment(monkeypatch) -> None:
    class _Client:
        def __init__(self) -> None:
            self.uploads = []
            self.sent = []

        async def upload_media(self, path, *, media_type, max_bytes, mime_type):
            self.uploads.append((path, media_type, max_bytes, mime_type))
            return {"token": "file-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "media-mid"}}}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._chat_target_types = {"user-1": "user"}
    adapter._media_max_bytes = 1024
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()
    monkeypatch.setattr(
        adapter,
        "extract_media",
        lambda _content: ([("/tmp/report.pdf", False)], "Отчёт"),
        raising=False,
    )
    monkeypatch.setattr(
        adapter,
        "filter_media_delivery_paths",
        lambda media_files: media_files,
        raising=False,
    )

    result = await adapter.send("user-1", "MEDIA:/tmp/report.pdf\nОтчёт")

    assert result.success is True
    assert adapter._client.uploads == [
        ("/tmp/report.pdf", "file", 1024, "application/pdf")
    ]
    assert adapter._client.sent[0][1] == "Отчёт"
    assert adapter._client.sent[0][2]["attachments"] == [
        {"type": "file", "payload": {"token": "file-token"}}
    ]


@pytest.mark.asyncio
async def test_group_session_id_is_translated_back_to_real_max_chat() -> None:
    class _Client:
        def __init__(self) -> None:
            self.sent = []

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "group-mid"}}}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._chat_target_types = {"group-1": "chat"}
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    result = await adapter.send("group-1::user::member-1", "hello")

    assert result.success is True
    assert adapter._client.sent[0][0] == "group-1"
    assert adapter._client.sent[0][2]["target_type"] == "chat"


@pytest.mark.asyncio
async def test_typing_actions_use_physical_chat_id_for_group_session() -> None:
    class _Client:
        def __init__(self) -> None:
            self.actions = []

        async def send_action(self, chat_id, action):
            self.actions.append((chat_id, action))
            return {"success": True}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    await adapter.send_typing("group-1::user::member-1")
    await adapter.stop_typing("group-1::user::member-1")

    assert adapter._client.actions == [("group-1", "typing"), ("group-1", "typing_off")]


@pytest.mark.asyncio
async def test_media_batch_delivers_successful_files_and_reports_partial_failure(monkeypatch) -> None:
    class _Client:
        def __init__(self) -> None:
            self.sent = []

        async def upload_media(self, path, *, media_type, max_bytes, mime_type):
            if str(path).endswith("bad.pdf"):
                raise max_adapter_module.MaxApiError("bad attachment")
            return {"token": "good-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "partial-mid"}}}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._chat_target_types = {"user-1": "user"}
    adapter._media_max_bytes = 1024
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()
    monkeypatch.setattr(
        adapter,
        "extract_media",
        lambda _content: ([
            ("/tmp/bad.pdf", False),
            ("/tmp/good.pdf", False),
        ], "files"),
        raising=False,
    )
    monkeypatch.setattr(adapter, "filter_media_delivery_paths", lambda media_files: media_files, raising=False)

    result = await adapter.send("user-1", "MEDIA:/tmp/bad.pdf MEDIA:/tmp/good.pdf")

    assert result.success is False
    assert result.error_kind == "partial_media"
    assert adapter._client.sent[0][2]["attachments"] == [
        {"type": "file", "payload": {"token": "good-token"}}
    ]


@pytest.mark.asyncio
async def test_media_batch_splits_file_from_image_video(monkeypatch) -> None:
    class _Client:
        def __init__(self) -> None:
            self.sent = []

        async def upload_media(self, path, *, media_type, max_bytes, mime_type):
            del path, max_bytes, mime_type
            return {"token": f"{media_type}-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            mid = f"mid-{len(self.sent)}"
            return {"message": {"body": {"mid": mid}}}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._chat_target_types = {"user-1": "user"}
    adapter._media_max_bytes = 1024
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    result = await adapter._send_media_files(
        "user-1",
        "mixed media",
        [
            ("photo.png", False),
            ("clip.mp4", False),
            ("report.pdf", False),
        ],
    )

    assert result.success is True
    assert len(adapter._client.sent) == 2
    assert [item["type"] for item in adapter._client.sent[0][2]["attachments"]] == [
        "image",
        "video",
    ]
    assert adapter._client.sent[1][1] == ""
    assert adapter._client.sent[1][2]["attachments"] == [
        {"type": "file", "payload": {"token": "file-token"}}
    ]


@pytest.mark.asyncio
async def test_standalone_sender_delivers_media_files(monkeypatch, tmp_path) -> None:
    class _Client:
        sent = []

        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def upload_media(self, path, *, media_type, max_bytes, mime_type):
            assert media_type == "file"
            assert max_bytes == 1024
            assert mime_type == "application/pdf"
            return {"token": "cron-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "cron-mid"}}}

        async def close(self):
            return None

    monkeypatch.setattr(max_adapter_module, "MaxClient", _Client)
    config = SimpleNamespace(
        token="secret-token",
        extra={"target_path": str(tmp_path / "targets.sqlite3"), "media_max_bytes": 1024},
    )
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-test")

    result = await standalone_send(
        config,
        "user-1",
        "Отчёт",
        media_files=[str(report)],
    )

    assert result["success"] is True
    assert _Client.sent[0][2]["attachments"] == [
        {"type": "file", "payload": {"token": "cron-token"}}
    ]


@pytest.mark.asyncio
async def test_adapter_direct_hermes_media_hooks_use_native_attachments(monkeypatch, tmp_path) -> None:
    class _Client:
        def __init__(self) -> None:
            self.uploads = []
            self.sent = []

        async def upload_media(self, path, *, media_type, max_bytes, mime_type, **kwargs):
            self.uploads.append((str(path), media_type, max_bytes, mime_type))
            return {"token": f"{media_type}-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": f"mid-{len(self.sent)}"}}}

    files = {
        "image": tmp_path / "картина.png",
        "file": tmp_path / "отчет.pdf",
        "audio": tmp_path / "голос.ogg",
        "video": tmp_path / "ролик.mp4",
    }
    for path in files.values():
        path.write_bytes(b"media")
    client = _Client()
    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = client
    adapter._chat_target_types = {"user-1": "user"}
    adapter._media_max_bytes = 1024
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()
    monkeypatch.setattr(adapter, "validate_media_delivery_path", lambda path: path)

    assert (await adapter.send_image_file("user-1", str(files["image"]))).success is True
    assert (await adapter.send_document("user-1", str(files["file"]), file_name="отчет.pdf")).success is True
    assert (await adapter.send_voice("user-1", str(files["audio"]))).success is True
    assert (await adapter.send_video("user-1", str(files["video"]))).success is True

    assert [item[1] for item in client.uploads] == ["image", "file", "audio", "video"]
    assert all(item[2]["attachments"][0]["payload"]["token"].endswith("-token") for item in client.sent)


@pytest.mark.asyncio
async def test_adapter_edit_message_keeps_chat_id_for_rate_limiter() -> None:
    class _Client:
        async def edit_message(self, message_id, text):
            assert message_id == "mid-1"
            assert text == "updated"
            return {"success": True}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    result = await adapter.edit_message("user-1", "mid-1", "updated")

    assert result.success is True
    assert result.message_id == "mid-1"


@pytest.mark.asyncio
async def test_adapter_remote_image_is_reuploaded_through_max_upload_flow() -> None:
    class _Client:
        def __init__(self) -> None:
            self.uploads = []
            self.sent = []

        async def download_media(self, url, *, max_bytes, allowed_hosts):
            assert url == "https://fal.media/result.png"
            assert max_bytes == 1024
            assert "fal.media" in allowed_hosts
            return b"png-bytes", "image/png"

        async def upload_media_bytes(self, data, *, filename, media_type, max_bytes, mime_type):
            self.uploads.append((data, filename, media_type, max_bytes, mime_type))
            return {"token": "remote-image-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "remote-mid"}}}

    client = _Client()
    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = client
    adapter._chat_target_types = {"user-1": "user"}
    adapter._media_max_bytes = 1024
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    result = await adapter.send_image("user-1", "https://fal.media/result.png", caption="Готово")

    assert result.success is True
    assert client.uploads == [(b"png-bytes", "result.png", "image", 1024, "image/png")]
    assert client.sent[0][2]["attachments"] == [
        {"type": "image", "payload": {"token": "remote-image-token"}}
    ]
