"""真实打 GitHub Trending 页面的回归测试。

GitHub 改 HTML 结构时这些测试会立刻失败，提醒及时更新解析器。
"""

from __future__ import annotations

from src.github.trending import build_trending_url, fetch_trending


class TestBuildUrl:
    """URL 构建是纯字符串拼接，但作为 fetch_trending 的前置仍然值得断言。"""

    def test_no_language(self):
        assert build_trending_url("") == "https://github.com/trending?since=daily"

    def test_with_language(self):
        assert (
            build_trending_url("python")
            == "https://github.com/trending/python?since=daily"
        )

    def test_quotes_special(self):
        assert "%23" in build_trending_url("c#")


class TestFetchTrending:
    async def test_returns_results_with_full_fields(self):
        """语言榜永远有热门项；断言所有字段齐全。"""
        repos = await fetch_trending("python", 5)
        assert repos, "trending should not be empty for 'python'"
        for r in repos:
            assert "/" in r.full_name, f"bad full_name: {r.full_name}"
            assert r.url == f"https://github.com/{r.full_name}"
            assert r.stars and r.stars != "N/A"
            assert r.forks and r.forks != "N/A"

    async def test_respects_max_results(self):
        repos = await fetch_trending("python", 3)
        assert len(repos) <= 3

    async def test_all_languages_works(self):
        """空语言 → 综合榜也应能返回数据。"""
        repos = await fetch_trending("", 3)
        assert repos, "global trending should not be empty"
        for r in repos:
            assert r.full_name
            assert r.url.startswith("https://github.com/")
