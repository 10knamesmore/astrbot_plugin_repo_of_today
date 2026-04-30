"""人格解析与 LLM 总结。"""

from __future__ import annotations

import asyncio
import re
from typing import cast

from astrbot.api import logger
from astrbot.api.star import Context

from . import LLM_CONCURRENCY, PLUGIN_NAME
from .models import RepoItem, RepoUpdate


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


async def summarize_commits_per_session(
    ctx: Context,
    session_umo: str,
    updates: list[RepoUpdate],
) -> list[tuple[RepoUpdate, str]]:
    """按会话统一解析人格，并发为每个仓库生成总结。"""
    if not updates:
        return []
    persona_prompt = await resolve_active_persona_prompt(ctx, session_umo)
    sem = asyncio.Semaphore(LLM_CONCURRENCY)

    async def _bounded(update: RepoUpdate) -> str:
        async with sem:
            return await summarize_commits(ctx, session_umo, update, persona_prompt)

    summaries = await asyncio.gather(
        *[_bounded(u) for u in updates], return_exceptions=True
    )
    out: list[tuple[RepoUpdate, str]] = []
    for update, summary in zip(updates, summaries, strict=False):
        if isinstance(summary, BaseException):
            logger.warning(
                "[%s] summarize_commits crashed for %s: %s",
                PLUGIN_NAME,
                update.full_name,
                summary,
            )
            out.append((update, ""))
            continue
        out.append((update, cast(str, summary)))
    return out
