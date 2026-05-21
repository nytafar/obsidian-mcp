import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from src.config import settings
from src.services import usage

logger = logging.getLogger(__name__)

_git_lock = asyncio.Lock()
_warned: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class GitBackupOutcome:
    status: str
    commit_sha: str | None = None
    reason: str | None = None


def reset_warning_cache() -> None:
    _warned.clear()


async def _run_git(
    vault_path: Path,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> GitCommandResult:
    git = shutil.which("git")
    if git is None:
        return GitCommandResult(127, "", "git executable not found")
    proc = await asyncio.create_subprocess_exec(
        git,
        "-C",
        str(vault_path),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout_b, stderr_b = await proc.communicate()
    return GitCommandResult(
        proc.returncode,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


def _warn_once(vault_path: Path, reason: str, message: str) -> None:
    key = (str(vault_path), reason)
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(message)


async def _log_outcome(
    tool: str,
    target: str,
    outcome: GitBackupOutcome,
    *,
    user_id: int | None,
) -> None:
    params = {
        "protected_tool": tool,
        "target": target,
        "status": outcome.status,
        "correlation_id": usage.current_correlation_id.get(),
    }
    if outcome.commit_sha:
        params["commit_sha"] = outcome.commit_sha
    if outcome.reason:
        params["reason"] = outcome.reason
    await usage.log_usage("git_backup", params, 0, 0, user_id=user_id)


def _safe_target(target: str) -> str:
    return target.replace("\n", " ").replace("\r", " ").strip()


async def snapshot_if_dirty(
    vault_path: Path,
    tool: str,
    target: str,
    *,
    user_id: int | None = None,
) -> GitBackupOutcome:
    """Snapshot uncommitted vault changes before a destructive MCP write.

    Failure to inspect or commit is intentionally non-fatal. The MCP write
    path must keep functioning even when git is unavailable or the worktree is
    temporarily unable to commit.
    """
    if settings.git_backup_enabled is False:
        return GitBackupOutcome("disabled", reason="configured_off")

    vault_path = Path(vault_path)
    try:
        async with _git_lock:
            if shutil.which("git") is None:
                outcome = GitBackupOutcome("disabled", reason="missing_git")
                _warn_once(vault_path, "missing_git", "Git backup disabled: git executable not found")
                await _log_outcome(tool, target, outcome, user_id=user_id)
                return outcome

            status = await _run_git(
                vault_path,
                ["status", "--porcelain", "--untracked-files=all"],
            )
            if status.returncode != 0:
                outcome = GitBackupOutcome("disabled", reason="not_git_repo")
                _warn_once(
                    vault_path,
                    "not_git_repo",
                    f"Git backup disabled for {vault_path}: not a usable git worktree",
                )
                await _log_outcome(tool, target, outcome, user_id=user_id)
                return outcome

            if not status.stdout.strip():
                outcome = GitBackupOutcome("clean")
                await _log_outcome(tool, target, outcome, user_id=user_id)
                return outcome

            add = await _run_git(vault_path, ["add", "-A", "--", "."])
            if add.returncode != 0:
                outcome = GitBackupOutcome("failed", reason="git_add_failed")
                _warn_once(
                    vault_path,
                    "git_add_failed",
                    f"Git backup failed for {vault_path}: git add failed: {add.stderr.strip()}",
                )
                await _log_outcome(tool, target, outcome, user_id=user_id)
                return outcome

            author_name = settings.git_author_name
            author_email = settings.git_author_email
            commit_env = os.environ.copy()
            commit_env.update({
                "GIT_AUTHOR_NAME": author_name,
                "GIT_AUTHOR_EMAIL": author_email,
                "GIT_COMMITTER_NAME": author_name,
                "GIT_COMMITTER_EMAIL": author_email,
            })
            message = (
                "incoming: snapshot of uncommitted working tree before "
                f"{tool}({_safe_target(target)})"
            )
            commit = await _run_git(vault_path, ["commit", "-m", message], env=commit_env)
            if commit.returncode != 0:
                outcome = GitBackupOutcome("failed", reason="git_commit_failed")
                _warn_once(
                    vault_path,
                    "git_commit_failed",
                    f"Git backup failed for {vault_path}: git commit failed: {commit.stderr.strip()}",
                )
                await _log_outcome(tool, target, outcome, user_id=user_id)
                return outcome

            rev = await _run_git(vault_path, ["rev-parse", "HEAD"])
            commit_sha = rev.stdout.strip() if rev.returncode == 0 else None
            outcome = GitBackupOutcome("committed", commit_sha=commit_sha)
            await _log_outcome(tool, target, outcome, user_id=user_id)
            return outcome
    except Exception as e:
        outcome = GitBackupOutcome("failed", reason="unexpected_error")
        _warn_once(
            vault_path,
            "unexpected_error",
            f"Git backup failed unexpectedly for {vault_path}: {e}",
        )
        await _log_outcome(tool, target, outcome, user_id=user_id)
        return outcome
