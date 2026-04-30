"""GitHub Trending 抓取与解析。"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup, Tag

from .. import TRENDING_BASE_URL, USER_AGENT
from ..models import RepoItem


def build_trending_url(language: str) -> str:
    """构建 GitHub Trending 请求地址。"""
    if language:
        return f"{TRENDING_BASE_URL}/{quote(language)}?since=daily"
    return f"{TRENDING_BASE_URL}?since=daily"


async def fetch_trending(language: str, max_results: int) -> list[RepoItem]:
    """抓取 Trending 页面并解析仓库条目。"""
    url = build_trending_url(language)
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(
        timeout=timeout,
        trust_env=True,
        headers={"User-Agent": USER_AGENT},
    ) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            html = await response.text()
    return await asyncio.to_thread(_parse_trending_html, html, max_results)


def _parse_trending_html(html: str, max_results: int) -> list[RepoItem]:
    """从 Trending HTML 中提取仓库信息。"""
    soup = BeautifulSoup(html, "html.parser")
    items: list[RepoItem] = []

    for article in soup.select("article.Box-row"):
        if len(items) >= max_results:
            break

        name_anchor = article.select_one("h2 a")
        if not name_anchor:
            continue

        href_value = name_anchor.get("href")
        if not isinstance(href_value, str):
            continue

        repo_path = href_value.strip("/")
        if not repo_path:
            continue

        full_name = re.sub(r"\s+", "", name_anchor.get_text(strip=True))
        description = ""
        description_tag = article.select_one("p")
        if description_tag:
            description = re.sub(r"\s+", " ", description_tag.get_text(strip=True))

        language = "Unknown"
        language_tag = article.select_one('[itemprop="programmingLanguage"]')
        if language_tag:
            language = language_tag.get_text(strip=True)

        stars = _extract_stat(article, "/stargazers")
        forks = _extract_stat(article, "/forks")
        stars_today = _extract_today_stars(article)

        items.append(
            RepoItem(
                full_name=full_name,
                description=description,
                language=language,
                stars=stars,
                forks=forks,
                stars_today=stars_today,
                url=f"https://github.com/{repo_path}",
            )
        )

    return items


def _extract_stat(article: Tag, href_suffix: str) -> str:
    """提取仓库统计字段（Star/Fork）。"""
    tag = article.select_one(f'a[href$="{href_suffix}"]')
    if not tag:
        return "N/A"
    return re.sub(r"\s+", "", tag.get_text(strip=True))


def _extract_today_stars(article: Tag) -> str:
    """提取今日新增 Star 字段。"""
    tag = article.select_one("span.d-inline-block.float-sm-right")
    if not tag:
        return "N/A"
    text = re.sub(r"\s+", " ", tag.get_text(strip=True)).lower()
    return text.replace("stars today", "").replace("star today", "").strip() or text
