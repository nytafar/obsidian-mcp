import asyncio
import shutil
import subprocess

import pytest

from src.services import git_backup


def _git(cwd, *args, env=None):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def _init_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "config", "user.email", "test@example.com")
    note = tmp_path / "note.md"
    note.write_text("initial\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")
    return note


def _commit_count(repo):
    return int(_git(repo, "rev-list", "--count", "HEAD").stdout.strip())


@pytest.fixture(autouse=True)
def _reset_git_backup(monkeypatch):
    git_backup.reset_warning_cache()
    monkeypatch.setattr(git_backup.settings, "git_backup_enabled", True, raising=False)
    monkeypatch.setattr(git_backup.settings, "git_author_name", "Hvelv MCP Agent", raising=False)
    monkeypatch.setattr(git_backup.settings, "git_author_email", "mcp-agent@hvelv.local", raising=False)

    async def _ignore_usage(*args, **kwargs):
        return None

    monkeypatch.setattr(git_backup.usage, "log_usage", _ignore_usage)


@pytest.mark.asyncio
async def test_clean_tree_skips_commit(tmp_path):
    _init_repo(tmp_path)
    before = _commit_count(tmp_path)

    outcome = await git_backup.snapshot_if_dirty(tmp_path, "edit_note", "note.md")

    assert outcome.status == "clean"
    assert _commit_count(tmp_path) == before


@pytest.mark.asyncio
async def test_dirty_tracked_file_is_committed(tmp_path):
    note = _init_repo(tmp_path)
    before = _commit_count(tmp_path)
    note.write_text("incoming change\n", encoding="utf-8")

    outcome = await git_backup.snapshot_if_dirty(tmp_path, "edit_note", "note.md")

    assert outcome.status == "committed"
    assert _commit_count(tmp_path) == before + 1
    assert _git(tmp_path, "show", "HEAD:note.md").stdout == "incoming change\n"
    assert "incoming: snapshot of uncommitted working tree before edit_note(note.md)" in _git(
        tmp_path, "show", "-s", "--format=%s", "HEAD"
    ).stdout


@pytest.mark.asyncio
async def test_untracked_file_is_included(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "new.md").write_text("incoming\n", encoding="utf-8")

    outcome = await git_backup.snapshot_if_dirty(tmp_path, "set_frontmatter", "note.md")

    assert outcome.status == "committed"
    assert "new.md" in _git(tmp_path, "ls-tree", "-r", "--name-only", "HEAD").stdout


@pytest.mark.asyncio
async def test_custom_author_is_applied(tmp_path, monkeypatch):
    note = _init_repo(tmp_path)
    note.write_text("incoming change\n", encoding="utf-8")
    monkeypatch.setattr(git_backup.settings, "git_author_name", "Custom Agent", raising=False)
    monkeypatch.setattr(git_backup.settings, "git_author_email", "agent@example.test", raising=False)

    await git_backup.snapshot_if_dirty(tmp_path, "delete_note", "note.md")

    assert _git(tmp_path, "show", "-s", "--format=%an <%ae>", "HEAD").stdout.strip() == (
        "Custom Agent <agent@example.test>"
    )


@pytest.mark.asyncio
async def test_missing_git_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(git_backup.shutil, "which", lambda _name: None)

    outcome = await git_backup.snapshot_if_dirty(tmp_path, "edit_note", "note.md")

    assert outcome.status == "disabled"


@pytest.mark.asyncio
async def test_non_repo_does_not_raise(tmp_path):
    (tmp_path / "note.md").write_text("incoming\n", encoding="utf-8")

    outcome = await git_backup.snapshot_if_dirty(tmp_path, "edit_note", "note.md")

    assert outcome.status == "disabled"


@pytest.mark.asyncio
async def test_failed_commit_does_not_raise(tmp_path, monkeypatch):
    async def _fake_run_git(vault_path, args, *, env=None):
        if args[0] == "status":
            return git_backup.GitCommandResult(0, " M note.md\n", "")
        if args[0] == "add":
            return git_backup.GitCommandResult(0, "", "")
        if args[0] == "commit":
            return git_backup.GitCommandResult(1, "", "commit failed")
        raise AssertionError(args)

    monkeypatch.setattr(git_backup, "_run_git", _fake_run_git)

    outcome = await git_backup.snapshot_if_dirty(tmp_path, "edit_note", "note.md")

    assert outcome.status == "failed"


@pytest.mark.asyncio
async def test_concurrent_snapshots_serialize_to_one_commit_for_one_dirty_state(tmp_path):
    note = _init_repo(tmp_path)
    before = _commit_count(tmp_path)
    note.write_text("incoming change\n", encoding="utf-8")

    outcomes = await asyncio.gather(
        git_backup.snapshot_if_dirty(tmp_path, "edit_note", "note.md"),
        git_backup.snapshot_if_dirty(tmp_path, "set_frontmatter", "note.md"),
    )

    assert [o.status for o in outcomes].count("committed") == 1
    assert _commit_count(tmp_path) == before + 1
