from __future__ import annotations

from src.formatting import (
    format_repo_update_message,
    format_trending_item,
    format_trending_message,
)
from src.models import RepoCommit, RepoItem, RepoUpdate


def _commit(sha="abcdef1234567", msg="feat: hi") -> RepoCommit:
    return RepoCommit(
        sha=sha,
        short_sha=sha[:7],
        message=msg,
        author="alice",
        committed_at="2026-04-30T12:00:00Z",
        url=f"https://github.com/owner/repo/commit/{sha}",
    )


def _repo() -> RepoItem:
    return RepoItem(
        full_name="foo/bar",
        description="desc",
        language="Python",
        stars="1.2k",
        forks="100",
        stars_today="42",
        url="https://github.com/foo/bar",
    )


class TestTrending:
    def test_format_item_basic(self):
        out = format_trending_item(1, _repo())
        assert "1. **foo/bar**" in out
        assert "desc" in out
        assert "Python" in out
        assert "1.2k" in out
        assert "https://github.com/foo/bar" in out

    def test_format_item_empty_description(self):
        repo = _repo()
        repo.description = ""
        out = format_trending_item(1, repo)
        assert "No description." in out

    def test_format_message_with_repos(self):
        out = format_trending_message("python", [_repo(), _repo()])
        assert out.startswith("## GitHub Trending (daily) - python")
        assert "1. **foo/bar**" in out
        assert "2. **foo/bar**" in out

    def test_format_message_empty(self):
        out = format_trending_message("rust", [])
        assert "No trending repositories found." in out


class TestRepoUpdateMessage:
    def test_with_summary_and_commits(self):
        update = RepoUpdate(
            full_name="owner/repo",
            commits=[_commit(msg="feat: A"), _commit(msg="fix: B")],
        )
        out = format_repo_update_message(update, "本批新增 A 与 B 修复")
        assert "## 📦 owner/repo 仓库更新" in out
        assert "本批新增 A 与 B 修复" in out
        assert "共 2 条新提交" in out
        assert "feat: A" in out
        assert "fix: B" in out
        assert "https://github.com/owner/repo/commits" in out

    def test_truncated_marker(self):
        update = RepoUpdate(
            full_name="owner/repo",
            commits=[_commit() for _ in range(50)],
            truncated=True,
        )
        out = format_repo_update_message(update, "summary")
        assert "显示最近 50 条" in out

    def test_no_commits_returns_empty(self):
        update = RepoUpdate(full_name="owner/repo", commits=[])
        assert format_repo_update_message(update, "anything") == ""

    def test_error_path(self):
        update = RepoUpdate(full_name="owner/repo", commits=[], error="not found")
        out = format_repo_update_message(update, "")
        assert "owner/repo" in out
        assert "not found" in out

    def test_summary_optional(self):
        update = RepoUpdate(full_name="o/r", commits=[_commit()])
        out = format_repo_update_message(update, "")
        assert "## 📦 o/r 仓库更新" in out
        assert "共 1 条新提交" in out
