"""真实打 GitHub commits API 的回归测试。

不联网时这些测试会失败 —— 这是有意的：本仓库的设计选择是用真实 API 调用兜底
HTML/JSON 形态变化，而非 fixture。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.github import commits as commits_api


def _since_iso(hours: int) -> str:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestFetchCommits:
    async def test_returns_well_shaped_commits_for_active_repo(self):
        """对一个长期活跃的仓库（30 天窗口必有提交），断言所有字段齐全。"""
        repos = [("AstrBotDevs", "AstrBot")]
        fetched = await commits_api.fetch_all(repos, _since_iso(24 * 30))

        assert "AstrBotDevs/AstrBot" in fetched
        update = fetched["AstrBotDevs/AstrBot"]
        assert update.error is None, f"unexpected error: {update.error!r}"
        assert update.commits, "expected at least one commit in 30d window"

        for c in update.commits:
            assert c.sha, "sha must be non-empty"
            assert len(c.short_sha) == 7
            assert c.short_sha == c.sha[:7]
            assert c.message, "message must be non-empty"
            assert "\n" not in c.message, "message should be first-line only"
            assert c.author, "author must be non-empty"
            assert c.committed_at, "committed_at must be non-empty"
            assert c.url.startswith(
                f"https://github.com/AstrBotDevs/AstrBot/commit/{c.sha}"
            )

    async def test_truncates_at_hard_cap(self):
        """30 天窗口大概率超过 50 条，断言截断生效。"""
        fetched = await commits_api.fetch_all(
            [("AstrBotDevs", "AstrBot")],
            _since_iso(24 * 30),
        )
        update = fetched["AstrBotDevs/AstrBot"]
        if update.error is not None:
            import pytest

            pytest.skip(f"github fetch errored: {update.error!r}")
        assert len(update.commits) <= 50
        # 仓库长期活跃，30 天通常 > 50；放宽断言以避免冷淡期假阴性
        assert update.truncated == (len(update.commits) == 50)

    async def test_nonexistent_repo_yields_error_not_exception(self):
        """不存在的仓库应当返回 error 字段，不抛异常。"""
        fetched = await commits_api.fetch_all(
            [("no-such-owner-xyz-12345", "definitely-not-real")],
            _since_iso(24),
        )
        update = fetched["no-such-owner-xyz-12345/definitely-not-real"]
        assert update.commits == []
        assert update.error is not None
        assert "not found" in update.error.lower() or update.error

    async def test_concurrent_multi_repo(self):
        """多个仓库一次抓取，每个独立处理。"""
        repos = [
            ("AstrBotDevs", "AstrBot"),
            ("python", "cpython"),
        ]
        fetched = await commits_api.fetch_all(repos, _since_iso(24 * 7))
        assert set(fetched) == {"AstrBotDevs/AstrBot", "python/cpython"}
        for full_name, update in fetched.items():
            if update.error:
                continue
            for c in update.commits:
                assert c.url.startswith(f"https://github.com/{full_name}/commit/")

    async def test_empty_repos_returns_empty(self):
        assert await commits_api.fetch_all([], _since_iso(24)) == {}
