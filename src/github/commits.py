"""通过 GitHub REST API 抓取追踪仓库最近提交。"""

from __future__ import annotations

import asyncio

import aiohttp
from astrbot.api import logger

from .. import (
    GITHUB_API_BASE,
    LLM_CONCURRENCY,
    PLUGIN_NAME,
    TRACK_HARD_CAP,
    USER_AGENT,
)
from ..models import RepoCommit, RepoUpdate


async def fetch_commits_since(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    since_iso: str,
    hard_cap: int = TRACK_HARD_CAP,
) -> RepoUpdate:
    """抓取某仓库默认分支自 since_iso 之后的所有提交（受 hard_cap 截断）。"""
    full_name = f"{owner}/{repo}"
    per_page = min(max(hard_cap, 1), 100)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits"
    params = {"since": since_iso, "per_page": str(per_page + 1)}
    try:
        async with session.get(url, params=params) as resp:
            if resp.status == 404:
                return RepoUpdate(full_name=full_name, commits=[], error="not found")
            if resp.status == 403:
                return RepoUpdate(
                    full_name=full_name,
                    commits=[],
                    error="rate limited",
                )
            resp.raise_for_status()
            data = await resp.json()
    except Exception as exc:
        logger.warning(
            "[%s] failed to fetch commits for %s: %s", PLUGIN_NAME, full_name, exc
        )
        return RepoUpdate(full_name=full_name, commits=[], error=str(exc))

    if not isinstance(data, list):
        return RepoUpdate(full_name=full_name, commits=[], error="unexpected payload")

    truncated = len(data) > hard_cap
    commits: list[RepoCommit] = []
    for entry in data[:hard_cap]:
        parsed = _parse_commit(entry)
        if parsed is not None:
            commits.append(parsed)
    return RepoUpdate(full_name=full_name, commits=commits, truncated=truncated)


async def fetch_all(
    repos: list[tuple[str, str]],
    since_iso: str,
) -> dict[str, RepoUpdate]:
    """并发抓取多个仓库，复用一个 ClientSession，受 LLM_CONCURRENCY 限速。"""
    if not repos:
        return {}
    timeout = aiohttp.ClientTimeout(total=20)
    sem = asyncio.Semaphore(LLM_CONCURRENCY)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with aiohttp.ClientSession(
        timeout=timeout, trust_env=True, headers=headers
    ) as session:

        async def _bounded(owner: str, repo: str) -> RepoUpdate:
            async with sem:
                return await fetch_commits_since(session, owner, repo, since_iso)

        tasks = [_bounded(owner, repo) for owner, repo in repos]
        updates = await asyncio.gather(*tasks)

    return {update.full_name: update for update in updates}


def _parse_commit(entry: dict) -> RepoCommit | None:
    """从 GitHub commits API JSON 条目解析为 RepoCommit。"""
    try:
        sha = str(entry.get("sha") or "")
        if not sha:
            return None
        commit = entry.get("commit") or {}
        message_raw = str(commit.get("message") or "").strip()
        first_line = message_raw.split("\n", 1)[0] if message_raw else ""

        author_block = commit.get("author") or {}
        author = str(author_block.get("name") or "unknown")
        committed_at = str(
            author_block.get("date") or commit.get("committer", {}).get("date") or ""
        )

        html_url = str(entry.get("html_url") or "")
        return RepoCommit(
            sha=sha,
            short_sha=sha[:7],
            message=first_line or "(no message)",
            author=author,
            committed_at=committed_at,
            url=html_url,
        )
    except Exception:
        return None
