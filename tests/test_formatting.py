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


def _commits(n: int) -> list[RepoCommit]:
    return [_commit(sha=f"sha{i:04d}aaaaaaaa", msg=f"commit {i}") for i in range(n)]


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
    def test_with_overview_and_explanations(self):
        update = RepoUpdate(
            full_name="owner/repo",
            commits=[
                _commit(sha="aaaaaaa1111111", msg="feat: A"),
                _commit(sha="bbbbbbb2222222", msg="fix: B"),
            ],
        )
        out = format_repo_update_message(
            update,
            overview="本批新增 A 与 B 修复",
            explanations=["新增 A 功能影响用户", "修复 B 崩溃"],
            show_count=10,
        )
        assert out.startswith("📦 owner/repo · 过去 24h · 2 条")
        assert "本批新增 A 与 B 修复" in out
        assert "• 新增 A 功能影响用户" in out
        assert "• 修复 B 崩溃" in out
        # 裸 URL，每条 commit 自己的 url 都出现
        assert "https://github.com/owner/repo/commit/aaaaaaa1111111" in out
        assert "https://github.com/owner/repo/commit/bbbbbbb2222222" in out
        # 末尾 commits 页 URL
        assert "https://github.com/owner/repo/commits" in out

    def test_no_markdown_links(self):
        update = RepoUpdate(full_name="owner/repo", commits=_commits(3))
        out = format_repo_update_message(
            update,
            overview="x",
            explanations=["a", "b", "c"],
            show_count=5,
        )
        # 不能出现 markdown 链接片段
        assert "](http" not in out
        assert "](" not in out

    def test_show_count_overflow(self):
        commits = _commits(20)
        update = RepoUpdate(full_name="owner/repo", commits=commits)
        out = format_repo_update_message(
            update,
            overview="o",
            explanations=[f"e{i}" for i in range(10)],
            show_count=10,
        )
        # 仅前 10 条 commit 的 URL 被渲染
        rendered_count = sum(1 for c in commits if c.url in out)
        assert rendered_count == 10
        assert "另有 10 条更新" in out

    def test_no_overflow_no_marker(self):
        commits = _commits(5)
        update = RepoUpdate(full_name="owner/repo", commits=commits)
        out = format_repo_update_message(
            update,
            overview="o",
            explanations=[f"e{i}" for i in range(5)],
            show_count=10,
        )
        assert "另有" not in out

    def test_explanations_falls_back_to_message(self):
        commits = [_commit(sha="aaaaaaa1111111", msg="feat: real-msg")]
        update = RepoUpdate(full_name="owner/repo", commits=commits)
        # 显式喂空字符串模拟 LLM 缺位
        out = format_repo_update_message(
            update,
            overview="",
            explanations=[""],
            show_count=10,
        )
        assert "feat: real-msg" in out

    def test_truncated_marker(self):
        commits = _commits(50)
        update = RepoUpdate(
            full_name="owner/repo",
            commits=commits,
            truncated=True,
        )
        out = format_repo_update_message(
            update,
            overview="summary",
            explanations=[f"e{i}" for i in range(10)],
            show_count=10,
        )
        assert "显示最近 50 条" in out

    def test_no_commits_returns_empty(self):
        update = RepoUpdate(full_name="owner/repo", commits=[])
        assert format_repo_update_message(update, "anything", [], show_count=10) == ""

    def test_error_path(self):
        update = RepoUpdate(full_name="owner/repo", commits=[], error="not found")
        out = format_repo_update_message(update, "", [], show_count=10)
        assert "owner/repo" in out
        assert "not found" in out

    def test_overview_optional(self):
        update = RepoUpdate(full_name="o/r", commits=_commits(1))
        out = format_repo_update_message(
            update,
            overview="",
            explanations=["just one explanation"],
            show_count=10,
        )
        assert out.startswith("📦 o/r · 过去 24h · 1 条")
        assert "just one explanation" in out
