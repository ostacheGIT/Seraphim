"""Tests for memory store and ingestion."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


# ─── Store ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_init_db_creates_table(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("seraphim.memory.store.DB_PATH", db_path):
        from seraphim.memory.store import init_db
        await init_db()
    assert db_path.exists()


@pytest.mark.asyncio
async def test_save_and_load_history(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("seraphim.memory.store.DB_PATH", db_path):
        from seraphim.memory.store import init_db, save_message, load_history
        await init_db()
        await save_message("sess1", "user", "Hello", "chat")
        await save_message("sess1", "assistant", "Hi there!", "chat")
        history = await load_history("sess1")

    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Hello"
    assert history[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_load_history_empty_session(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("seraphim.memory.store.DB_PATH", db_path):
        from seraphim.memory.store import init_db, load_history
        await init_db()
        history = await load_history("nonexistent_session")
    assert history == []


@pytest.mark.asyncio
async def test_list_sessions(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("seraphim.memory.store.DB_PATH", db_path):
        from seraphim.memory.store import init_db, save_message, list_sessions
        await init_db()
        await save_message("sess_a", "user", "First question", "chat")
        await save_message("sess_b", "user", "Second question", "react")
        sessions = await list_sessions()

    session_ids = {s["session"] for s in sessions}
    assert "sess_a" in session_ids
    assert "sess_b" in session_ids


@pytest.mark.asyncio
async def test_delete_session(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("seraphim.memory.store.DB_PATH", db_path):
        from seraphim.memory.store import init_db, save_message, delete_session, load_history
        await init_db()
        await save_message("to_delete", "user", "temp", "chat")
        await delete_session("to_delete")
        history = await load_history("to_delete")
    assert history == []


# ─── Ingestion ────────────────────────────────────────────────────────────────

def _make_mock_backend():
    backend = MagicMock()
    backend.store.return_value = "doc_id_1"
    return backend


def test_ingest_text_returns_doc_ids():
    from seraphim.memory.ingest import ingest_text
    from seraphim.memory.chunking import ChunkConfig
    backend = _make_mock_backend()
    long_text = " ".join(["word"] * 60)
    ids = ingest_text(long_text, backend, source="test.txt", config=ChunkConfig(min_chunk_size=1))
    assert isinstance(ids, list)
    assert len(ids) >= 1
    backend.store.assert_called()


def test_ingest_file_txt(tmp_path):
    from seraphim.memory.ingest import ingest_file
    from seraphim.memory.chunking import ChunkConfig
    backend = _make_mock_backend()
    f = tmp_path / "doc.txt"
    f.write_text(" ".join(["word"] * 60), encoding="utf-8")
    ids = ingest_file(f, backend, config=ChunkConfig(min_chunk_size=1))
    assert isinstance(ids, list)
    assert len(ids) >= 1


def test_ingest_directory_counts(tmp_path):
    from seraphim.memory.ingest import ingest_directory
    from seraphim.memory.chunking import ChunkConfig
    backend = _make_mock_backend()
    content = " ".join(["word"] * 60)
    (tmp_path / "a.txt").write_text(content, encoding="utf-8")
    (tmp_path / "b.md").write_text(content, encoding="utf-8")
    (tmp_path / "skip.csv").write_text("should,be,skipped", encoding="utf-8")
    count = ingest_directory(tmp_path, backend, config=ChunkConfig(min_chunk_size=1))
    assert count >= 2


def test_ingest_directory_skips_errors(tmp_path):
    from seraphim.memory.ingest import ingest_directory

    backend = MagicMock()
    backend.store.side_effect = RuntimeError("disk full")

    (tmp_path / "bad.txt").write_text("content", encoding="utf-8")
    count = ingest_directory(tmp_path, backend)
    assert count == 0
