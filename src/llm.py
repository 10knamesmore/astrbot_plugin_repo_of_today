"""人格解析与 LLM 总结。"""

from __future__ import annotations

import asyncio
import re
from typing import cast

from astrbot.api import logger
from astrbot.api.star import Context

from . import LLM_CONCURRENCY, PLUGIN_NAME
from .models import RepoCommit, RepoItem, RepoUpdate

_NUMBERED_LINE_RE = re.compile(r"^\s*(\d+)[.、)]\s*(.+?)\s*$")


async def resolve_active_persona_prompt(ctx: Context, session_umo: str) -> str:
    """解析当前会话启用人格并返回 system_prompt。"""
    try:
        curr_cid = await ctx.conversation_manager.get_curr_conversation_id(session_umo)
        conversation_persona_id = None
        if curr_cid:
            conv = await ctx.conversation_manager.get_conversation(
                unified_msg_origin=session_umo,
                conversation_id=curr_cid,
            )
            if conv:
                conversation_persona_id = conv.persona_id

        provider_settings = ctx.get_config(umo=session_umo).get(
            "provider_settings",
            {},
        )
        platform_name = session_umo.split(":", 1)[0] if ":" in session_umo else ""
        _, persona, _, _ = await ctx.persona_manager.resolve_selected_persona(
            umo=session_umo,
            conversation_persona_id=conversation_persona_id,
            platform_name=platform_name,
            provider_settings=provider_settings,
        )
        if persona and persona.get("prompt"):
            return str(persona["prompt"])
    except Exception as exc:
        logger.warning(
            "[%s] failed to resolve active persona prompt: %s", PLUGIN_NAME, exc
        )
    return ""


async def generate_persona_opening_line(ctx: Context, session_umo: str) -> str:
    """基于当前启用人格生成本次运行的开场语。"""
    try:
        provider_id = await ctx.get_current_chat_provider_id(umo=session_umo)
        persona_prompt = await resolve_active_persona_prompt(ctx, session_umo)
        if not persona_prompt:
            return ""
        llm_resp = await ctx.llm_generate(
            chat_provider_id=provider_id,
            prompt=(
                "你将要进行repo of today的推荐，写一句本次推荐开始前的中文开场白（8-20字）。"
                "要求自然、简短，不要使用 markdown，不要加引号。"
            ),
            system_prompt=persona_prompt,
            temperature=1,
            max_tokens=60,
        )
        return re.sub(r"\s+", " ", (llm_resp.completion_text or "").strip())
    except Exception as exc:
        logger.warning(
            "[%s] failed to generate persona opening line: %s", PLUGIN_NAME, exc
        )
        return ""


async def enhance_repos_with_ai(
    ctx: Context, repos: list[RepoItem], session_umo: str
) -> list[RepoItem]:
    """使用 LLM 为仓库生成简短中文描述，并替换原始描述。"""
    if not repos:
        return repos

    persona_prompt = await resolve_active_persona_prompt(ctx, session_umo)
    sem = asyncio.Semaphore(LLM_CONCURRENCY)

    async def _bounded(repo: RepoItem) -> str:
        async with sem:
            return await _generate_ai_description(
                ctx, repo, session_umo, persona_prompt
            )

    tasks = [_bounded(repo) for repo in repos]
    ai_desc_list = await asyncio.gather(*tasks, return_exceptions=True)

    enhanced: list[RepoItem] = []
    for repo, ai_desc in zip(repos, ai_desc_list, strict=False):
        if isinstance(ai_desc, BaseException):
            logger.warning(
                "[%s] ai description failed for %s: %s",
                PLUGIN_NAME,
                repo.full_name,
                ai_desc,
            )
            enhanced.append(repo)
            continue
        ai_desc_text = cast(str, ai_desc)
        enhanced.append(
            RepoItem(
                full_name=repo.full_name,
                description=ai_desc_text,
                language=repo.language,
                stars=repo.stars,
                forks=repo.forks,
                stars_today=repo.stars_today,
                url=repo.url,
            )
        )
    return enhanced


async def _generate_ai_description(
    ctx: Context,
    repo: RepoItem,
    session_umo: str,
    persona_prompt: str,
) -> str:
    """调用当前会话 LLM 为单个仓库生成一句话描述。"""
    try:
        provider_id = await ctx.get_current_chat_provider_id(umo=session_umo)
        prompt = (
            "请基于以下 GitHub 仓库信息，生成 1 句中文描述（20-45 字），"
            "突出用途和亮点，不要使用 markdown。\n"
            f"仓库：{repo.full_name}\n"
            f"语言：{repo.language}\n"
            f"原简介：{repo.description or '无'}\n"
            f"Stars：{repo.stars}\n"
            f"Forks：{repo.forks}\n"
            f"今日新增 Stars：{repo.stars_today}\n"
            f"链接：{repo.url}"
        )
        llm_resp = await ctx.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=persona_prompt or None,
            temperature=0.3,
            max_tokens=120,
        )
        ai_text = (llm_resp.completion_text or "").strip()
        ai_text = re.sub(r"\s+", " ", ai_text)
        if ai_text:
            return ai_text
    except Exception as exc:
        logger.warning(
            "[%s] llm_generate failed for %s: %s",
            PLUGIN_NAME,
            repo.full_name,
            exc,
        )
    return repo.description or "暂无描述。"


async def summarize_commits(
    ctx: Context,
    session_umo: str,
    update: RepoUpdate,
    persona_prompt: str,
) -> str:
    """让 LLM 总结某仓库窗口内的多条 commit。失败时回落为前几条 message 拼接。"""
    if not update.commits:
        return ""
    try:
        provider_id = await ctx.get_current_chat_provider_id(umo=session_umo)
        commit_lines = "\n".join(
            f"- [{c.short_sha}] {c.message} —— {c.author} @ {c.committed_at}"
            for c in update.commits
        )
        prompt = (
            f"以下是 GitHub 仓库 {update.full_name} 过去 24 小时的 commit 列表，"
            "请用 1-2 句中文总结本批改动的主题与亮点（不超过 80 字），"
            "突出新增功能、修复或重要重构，不要使用 markdown，不要罗列所有 commit。\n"
            f"{commit_lines}"
        )
        llm_resp = await ctx.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=persona_prompt or None,
            temperature=0.3,
            max_tokens=160,
        )
        text = re.sub(r"\s+", " ", (llm_resp.completion_text or "").strip())
        if text:
            return text
    except Exception as exc:
        logger.warning(
            "[%s] summarize_commits failed for %s: %s",
            PLUGIN_NAME,
            update.full_name,
            exc,
        )
    preview = "; ".join(c.message for c in update.commits[:3])
    return f"共 {len(update.commits)} 条提交：{preview}"


async def explain_commits(
    ctx: Context,
    session_umo: str,
    commits: list[RepoCommit],
    full_name: str,
    persona_prompt: str,
) -> list[str]:
    """让 LLM 为每条 commit 输出一句中文说明。返回值与 commits 等长。

    解析失败的位置回落为对应 commit 的原 message。
    """
    if not commits:
        return []
    fallback = [c.message for c in commits]
    try:
        provider_id = await ctx.get_current_chat_provider_id(umo=session_umo)
        commit_lines = "\n".join(
            f"{idx}. [{c.short_sha}] {c.message} —— {c.author}"
            for idx, c in enumerate(commits, 1)
        )
        prompt = (
            f"以下是 GitHub 仓库 {full_name} 过去 24 小时最新的 {len(commits)} 条 commit。\n"
            "请为每条 commit 用一句中文（不超过 30 字）说明它做了什么，"
            '重在传达"对用户/开发者的影响"，不要复述 commit 标题，不要使用 markdown。\n'
            "严格按以下格式逐条输出，每条仅一行，不要任何前后缀文字：\n"
            "1. <说明>\n"
            "2. <说明>\n"
            "...\n\n"
            "输入：\n"
            f"{commit_lines}"
        )
        llm_resp = await ctx.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=persona_prompt or None,
            temperature=0.3,
            max_tokens=max(200, 60 * len(commits)),
        )
        text = (llm_resp.completion_text or "").strip()
        if not text:
            return fallback
    except Exception as exc:
        logger.warning(
            "[%s] explain_commits failed for %s: %s", PLUGIN_NAME, full_name, exc
        )
        return fallback

    parsed: list[str | None] = [None] * len(commits)
    for raw_line in text.splitlines():
        m = _NUMBERED_LINE_RE.match(raw_line)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(parsed) and parsed[idx] is None:
            parsed[idx] = re.sub(r"\s+", " ", m.group(2))
    return [parsed[i] or fallback[i] for i in range(len(commits))]


async def process_updates_per_session(
    ctx: Context,
    session_umo: str,
    updates: list[RepoUpdate],
    show_count: int,
) -> list[tuple[RepoUpdate, str, list[str]]]:
    """按会话统一解析人格，并发为每个 update 生成 (overview, per_commit_explanations)。

    explanations 长度 = min(len(update.commits), show_count)；只对将要展示的前 N 条调
    `explain_commits`，避免为不展示的尾部 commit 浪费 token。
    """
    if not updates:
        return []
    persona_prompt = await resolve_active_persona_prompt(ctx, session_umo)
    sem = asyncio.Semaphore(LLM_CONCURRENCY)

    async def _bounded_overview(update: RepoUpdate) -> str:
        async with sem:
            return await summarize_commits(ctx, session_umo, update, persona_prompt)

    async def _bounded_explanations(update: RepoUpdate) -> list[str]:
        head = update.commits[:show_count]
        async with sem:
            return await explain_commits(
                ctx, session_umo, head, update.full_name, persona_prompt
            )

    overview_task = asyncio.gather(
        *[_bounded_overview(u) for u in updates], return_exceptions=True
    )
    explanations_task = asyncio.gather(
        *[_bounded_explanations(u) for u in updates], return_exceptions=True
    )
    overviews, explanations_lists = await asyncio.gather(
        overview_task, explanations_task
    )

    out: list[tuple[RepoUpdate, str, list[str]]] = []
    for update, overview, explanations in zip(
        updates, overviews, explanations_lists, strict=False
    ):
        if isinstance(overview, BaseException):
            logger.warning(
                "[%s] summarize_commits crashed for %s: %s",
                PLUGIN_NAME,
                update.full_name,
                overview,
            )
            overview_text = ""
        else:
            overview_text = cast(str, overview)
        if isinstance(explanations, BaseException):
            logger.warning(
                "[%s] explain_commits crashed for %s: %s",
                PLUGIN_NAME,
                update.full_name,
                explanations,
            )
            explanations_list = [c.message for c in update.commits[:show_count]]
        else:
            explanations_list = cast(list[str], explanations)
        out.append((update, overview_text, explanations_list))
    return out
