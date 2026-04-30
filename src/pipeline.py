"""业务流编排：trending 与追踪仓库 commits。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context

from . import MAX_RESULTS_LIMIT, PLUGIN_NAME, TRACK_WINDOW_HOURS
from .config import build_runtime_config, tracked_repos
from .formatting import format_repo_update_message, format_trending_message
from .github import commits as commits_api
from .github.trending import fetch_trending
from .llm import (
    enhance_repos_with_ai,
    generate_persona_opening_line,
    summarize_commits_per_session,
)
from .models import PushConfig, RepoItem, RepoUpdate


async def run_repo_today_once(
    ctx: Context,
    config: AstrBotConfig,
    session_umo: str,
) -> list[str]:
    """单会话一次性执行：抓 trending → LLM 增强 → 返回消息列表。"""
    push_config = build_runtime_config(config, session_umo)
    return await _build_trending_messages_for_session(ctx, push_config)


async def run_trending(
    ctx: Context,
    config: AstrBotConfig,
    sessions: list[str],
) -> dict[str, list[str]]:
    """cron 用：共享一次 trending 抓取，按会话独立 LLM 增强。"""
    if not sessions:
        return {}
    base_config = build_runtime_config(config, sessions[0])
    languages = base_config.languages or []
    max_results = max(1, min(base_config.push_count, MAX_RESULTS_LIMIT))
    fetch_keys = languages if languages else [""]

    fetched: dict[str, list[RepoItem] | Exception] = {}
    for key in fetch_keys:
        try:
            fetched[key] = await fetch_trending(key, max_results)
        except Exception as exc:
            logger.exception(
                "[%s] failed to fetch trending for %s",
                PLUGIN_NAME,
                key or "all",
            )
            fetched[key] = exc

    out: dict[str, list[str]] = {}
    for umo in sessions:
        try:
            session_config = build_runtime_config(config, umo)
            messages = await _build_trending_messages_with_cached(
                ctx, session_config, fetched
            )
        except Exception:
            logger.exception(
                "[%s] failed to build trending messages for %s", PLUGIN_NAME, umo
            )
            continue
        out[umo] = messages
    return out


async def run_repo_track_once(
    ctx: Context,
    config: AstrBotConfig,
    session_umo: str,
) -> list[str]:
    """单会话一次性执行：抓追踪仓库 24h 提交 → LLM 总结 → 返回消息列表。"""
    repos = tracked_repos(config)
    if not repos:
        return []
    since_iso = _since_iso()
    fetched = await commits_api.fetch_all(repos, since_iso)
    return await _build_track_messages(ctx, session_umo, fetched)


async def run_track(
    ctx: Context,
    config: AstrBotConfig,
    sessions: list[str],
) -> dict[str, list[str]]:
    """cron 用：共享一次 commits 抓取，按会话独立 LLM 总结。"""
    if not sessions:
        return {}
    repos = tracked_repos(config)
    if not repos:
        return {}

    since_iso = _since_iso()
    fetched = await commits_api.fetch_all(repos, since_iso)

    out: dict[str, list[str]] = {}
    for umo in sessions:
        try:
            messages = await _build_track_messages(ctx, umo, fetched)
        except Exception:
            logger.exception(
                "[%s] failed to build track messages for %s", PLUGIN_NAME, umo
            )
            continue
        if messages:
            out[umo] = messages
    return out


async def _build_trending_messages_for_session(
    ctx: Context,
    config: PushConfig,
) -> list[str]:
    """根据配置构建 trending 推送消息。"""
    languages = config.languages or []
    max_results = max(1, min(config.push_count, MAX_RESULTS_LIMIT))
    opening_line = await generate_persona_opening_line(ctx, config.session_umo)
    messages: list[str] = []
    if opening_line:
        messages.append(f"🗣️ {opening_line}")

    if not languages:
        try:
            repos = await fetch_trending("", max_results)
            repos = await enhance_repos_with_ai(ctx, repos, config.session_umo)
            messages.append(format_trending_message("all languages", repos))
        except Exception as exc:
            messages.append(f"## all languages\nFailed to fetch: {exc}")
        return messages

    for language in languages:
        try:
            repos = await fetch_trending(language, max_results)
            repos = await enhance_repos_with_ai(ctx, repos, config.session_umo)
        except Exception as exc:
            messages.append(f"## {language}\nFailed to fetch: {exc}")
            continue
        messages.append(format_trending_message(language, repos))
    return messages


async def _build_trending_messages_with_cached(
    ctx: Context,
    config: PushConfig,
    fetched: dict[str, list[RepoItem] | Exception],
) -> list[str]:
    """复用已抓取的 trending 数据，按会话做 persona+LLM 增强。"""
    languages = config.languages or []
    max_results = max(1, min(config.push_count, MAX_RESULTS_LIMIT))
    opening_line = await generate_persona_opening_line(ctx, config.session_umo)
    messages: list[str] = []
    if opening_line:
        messages.append(f"🗣️ {opening_line}")

    if not languages:
        data = fetched.get("")
        if isinstance(data, Exception):
            messages.append(f"## all languages\nFailed to fetch: {data}")
            return messages
        repos = list(data or [])[:max_results]
        repos = await enhance_repos_with_ai(ctx, repos, config.session_umo)
        messages.append(format_trending_message("all languages", repos))
        return messages

    for lang in languages:
        data = fetched.get(lang)
        if isinstance(data, Exception):
            messages.append(f"## {lang}\nFailed to fetch: {data}")
            continue
        repos = list(data or [])[:max_results]
        repos = await enhance_repos_with_ai(ctx, repos, config.session_umo)
        messages.append(format_trending_message(lang, repos))
    return messages


async def _build_track_messages(
    ctx: Context,
    session_umo: str,
    fetched: dict[str, RepoUpdate],
) -> list[str]:
    """对每个仓库的 RepoUpdate 做 LLM 总结并组装为消息列表（无更新仓库会被跳过）。"""
    interesting = [u for u in fetched.values() if u.commits or u.error]
    has_commits = [u for u in interesting if u.commits]
    if not has_commits:
        return []
    summaries = await summarize_commits_per_session(ctx, session_umo, has_commits)
    summary_map = {update.full_name: text for update, text in summaries}

    messages: list[str] = []
    for update in interesting:
        text = format_repo_update_message(update, summary_map.get(update.full_name, ""))
        if text:
            messages.append(text)
    return messages


def _since_iso() -> str:
    """返回 (now - TRACK_WINDOW_HOURS) 的 UTC ISO8601 字符串。"""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=TRACK_WINDOW_HOURS)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")
