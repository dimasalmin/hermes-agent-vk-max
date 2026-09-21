from __future__ import annotations

from plugins.max.polling_state import MaxTargetStore, PollingMarkerStore


def test_marker_survives_store_reopen(tmp_path) -> None:
    path = tmp_path / "polling.sqlite3"
    first = PollingMarkerStore(path)
    assert first.get() is None
    first.set(123)
    first.close()

    second = PollingMarkerStore(path)
    assert second.get() == 123
    second.close()


def test_target_type_survives_store_reopen(tmp_path) -> None:
    path = tmp_path / "targets.sqlite3"
    first = MaxTargetStore(path)
    assert first.get("chat-1") is None
    first.set("chat-1", "chat")
    first.close()

    second = MaxTargetStore(path)
    assert second.get("chat-1") == "chat"
    second.close()


def test_polling_batch_commits_updates_and_marker_together(tmp_path) -> None:
    path = tmp_path / "polling.sqlite3"
    store = PollingMarkerStore(path)
    update = {"update_type": "message_created", "update_id": 7}

    store.accept_batch([update], 99)

    assert store.get() == 99
    pending = store.claim_next()
    assert pending == update
    store.mark_processed(update)
    summary = store.status_summary()
    assert summary["processed"] == 1
    assert summary["pending"] == 0
    store.close()


def test_polling_failed_update_is_visible_and_not_replayed(tmp_path) -> None:
    path = tmp_path / "polling.sqlite3"
    store = PollingMarkerStore(path)
    update = {"update_type": "message_created", "update_id": 8}

    store.accept_batch([update], 100)
    assert store.claim_next() == update
    store.mark_failed(update, "MAX API 429")

    assert store.claim_next() is None
    summary = store.status_summary()
    assert summary["failed"] == 1
    assert summary["last_error"] == "MAX API 429"
    store.close()
