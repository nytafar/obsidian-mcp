import pytest

from src.auth.session import current_user_id
from src.config import settings
from src.mcp_server import tools
from src.mcp_server.auth import current_permission
from src.services import vault


class _FakeSession:
    async def execute(self, _stmt):
        return None

    async def commit(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


@pytest.fixture
def writable_context(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(tools, "async_session", lambda: _FakeSession())

    async def _ignore_usage(*args, **kwargs):
        return None

    monkeypatch.setattr(tools.usage, "log_usage", _ignore_usage)
    token_perm = current_permission.set("readwrite")
    token_user = current_user_id.set(None)
    try:
        yield tmp_path
    finally:
        current_permission.reset(token_perm)
        current_user_id.reset(token_user)


@pytest.fixture
def backup_calls(monkeypatch):
    calls = []

    async def _record(vault_path, tool, target, *, user_id=None):
        calls.append((vault_path, tool, target, user_id))
        return None

    monkeypatch.setattr(vault.git_backup, "snapshot_if_dirty", _record)
    return calls


@pytest.mark.asyncio
async def test_create_note_does_not_trigger_backup(writable_context, backup_calls):
    result = await tools.create_note_impl("new.md", "hello\n")

    assert result == "Created note: new.md"
    assert backup_calls == []


@pytest.mark.asyncio
async def test_edit_note_triggers_backup_before_write(writable_context, backup_calls):
    note = writable_context / "note.md"
    note.write_text("old\n", encoding="utf-8")

    result = await tools.edit_note_impl("note.md", "new\n")

    assert result == "Updated note: note.md"
    assert backup_calls == [(writable_context, "edit_note", "note.md", None)]
    assert note.read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_edit_note_dry_run_skips_backup(writable_context, backup_calls):
    (writable_context / "note.md").write_text("old\n", encoding="utf-8")

    result = await tools.edit_note_impl("note.md", "new\n", dry_run=True)

    assert "---" in result
    assert backup_calls == []


@pytest.mark.asyncio
async def test_set_frontmatter_triggers_backup(writable_context, backup_calls):
    note = writable_context / "note.md"
    note.write_text("---\nstatus: draft\n---\nbody\n", encoding="utf-8")

    result = await tools.set_frontmatter_impl("note.md", updates={"status": "done"})

    assert result == "Updated frontmatter in note.md (set: status)"
    assert backup_calls == [(writable_context, "set_frontmatter", "note.md", None)]


@pytest.mark.asyncio
async def test_move_note_triggers_backup(writable_context, backup_calls):
    src = writable_context / "old.md"
    src.write_text("old\n", encoding="utf-8")

    result = await tools.move_note_impl("old.md", "new.md")

    assert "Moved old.md" in result
    assert backup_calls == [(writable_context, "move_note", "old.md -> new.md", None)]
    assert not src.exists()
    assert (writable_context / "new.md").read_text(encoding="utf-8") == "old\n"


@pytest.mark.asyncio
async def test_delete_note_triggers_backup(writable_context, backup_calls):
    note = writable_context / "note.md"
    note.write_text("old\n", encoding="utf-8")

    result = await tools.delete_note_impl("note.md", permanent=True)

    assert result == "Permanently deleted: note.md"
    assert backup_calls == [(writable_context, "delete_note", "note.md", None)]
    assert not note.exists()


@pytest.mark.asyncio
async def test_git_backup_log_shares_tool_correlation_id(writable_context, monkeypatch):
    logs = []

    async def _log_usage(tool, params, duration_ms, response_size, *, user_id=None):
        logs.append((tool, params))

    async def _fake_backup(vault_path, tool, target, *, user_id=None):
        await tools.usage.log_usage(
            "git_backup",
            {
                "protected_tool": tool,
                "target": target,
                "correlation_id": tools.usage.current_correlation_id.get(),
            },
            0,
            0,
            user_id=user_id,
        )

    monkeypatch.setattr(tools.usage, "log_usage", _log_usage)
    monkeypatch.setattr(vault.git_backup, "snapshot_if_dirty", _fake_backup)
    (writable_context / "note.md").write_text("old\n", encoding="utf-8")

    await tools.edit_note_impl("note.md", "new\n")

    git_log = next(params for tool, params in logs if tool == "git_backup")
    tool_log = next(params for tool, params in logs if tool == "edit_note")
    assert git_log["correlation_id"] == tool_log["correlation_id"]
