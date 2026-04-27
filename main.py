from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from bs4 import BeautifulSoup, Tag

PLUGIN_NAME = "astrbot_plugin_repo_of_today"
DEFAULT_PUSH_TIME = "09:00"
DEFAULT_PUSH_COUNT = 5
MAX_RESULTS_LIMIT = 10
TRENDING_BASE_URL = "https://github.com/trending"


@dataclass(slots=True)
class RepoItem:
    """GitHub 仓库条目。"""

    full_name: str
    """仓库全名，格式通常为 owner/repo。"""
    description: str
    """仓库简介。"""
    language: str
    """主开发语言。"""
    stars: str
    """累计 Star 数。"""
    forks: str
    """累计 Fork 数。"""
    stars_today: str
    """今日新增 Star 数。"""
    url: str
    """仓库链接。"""


@dataclass(slots=True)
class PushConfig:
    """单次推送的运行时配置。"""

    session_umo: str
    """会话唯一标识（UMO）。"""
    push_time: str = DEFAULT_PUSH_TIME
    """每日推送时间，格式为 HH:MM。"""
    languages: list[str] | None = None
    """关注语言列表；空列表表示全语言。"""
    push_count: int = DEFAULT_PUSH_COUNT
    """每种语言推送的仓库数量。"""


class Main(Star):
    """Repo Of Today 插件主类。

    全部行为由 `_conf_schema.json` 配置驱动：
    1. `/repo_today` 手动拉取一次（无参数，使用当前配置）。
    2. `enabled=true` 时按 `push_time` 给 `target_sessions` 中的每个会话注册定时推送。
    3. 插件重载/卸载时统一管理 cron 任务的注册与清理。
    """

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        """初始化插件上下文和任务锁。"""
        super().__init__(context)
        self.context = context
        self.config = config
        self._job_lock = asyncio.Lock()
        self._job_ids: list[str] = []

    @property
    def ctx(self) -> Context:
        """返回具备完整类型提示的 Context。"""
        return cast(Context, self.context)

    async def initialize(self) -> None:
        """插件加载时按配置注册定时任务。"""
        if not bool(self.config.get("enabled", False)):
            return
        async with self._job_lock:
            for umo in self._target_sessions():
                try:
                    await self._schedule_for(umo)
                except Exception:
                    logger.exception(
                        "[%s] failed to schedule cron job for %s",
                        PLUGIN_NAME,
                        umo,
                    )

    async def terminate(self) -> None:
        """插件卸载时清理已注册任务，避免重复触发。"""
        async with self._job_lock:
            for job_id in list(self._job_ids):
                await self._delete_job(job_id)
            self._job_ids.clear()

    @filter.command("repo_today")
    async def repo_today(self, event: AstrMessageEvent):
        """手动查询 GitHub 当日热门仓库（基于当前配置）。"""
        runtime_config = self._build_runtime_config(event.unified_msg_origin)
        typing_task = asyncio.create_task(self._typing_heartbeat(event))

        try:
            try:
                messages = await self._build_push_messages(runtime_config)
            except Exception as exc:
                logger.exception("[%s] failed to fetch trending repos", PLUGIN_NAME)
                yield event.plain_result(
                    f"Failed to fetch trending repositories: {exc}"
                )
                return
            for msg in messages:
                yield event.plain_result(msg)
            await self._maybe_append_history(event.unified_msg_origin, messages)
        finally:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task
            with contextlib.suppress(Exception):
                await event.stop_typing()

    def _target_sessions(self) -> list[str]:
        """从配置中读取规范化后的 UMO 列表。"""
        raw = self.config.get("target_sessions", []) or []
        if not isinstance(raw, list):
            return []
        return [
            str(item).strip() for item in raw if isinstance(item, str) and item.strip()
        ]

    def _build_runtime_config(self, session_umo: str) -> PushConfig:
        """根据当前配置和 UMO 组装单次运行的 PushConfig。"""
        push_time = (
            self._normalize_time(str(self.config.get("push_time", DEFAULT_PUSH_TIME)))
            or DEFAULT_PUSH_TIME
        )

        raw_count = self.config.get("push_count", DEFAULT_PUSH_COUNT)
        try:
            push_count = int(raw_count)
        except (TypeError, ValueError):
            push_count = DEFAULT_PUSH_COUNT
        push_count = max(1, min(push_count, MAX_RESULTS_LIMIT))

        raw_languages = self.config.get("languages", []) or []
        if not isinstance(raw_languages, list):
            raw_languages = []
        languages = [
            self._normalize_language(str(item))
            for item in raw_languages
            if isinstance(item, str) and item.strip()
        ]
        languages = [lang for lang in languages if lang]

        return PushConfig(
            session_umo=session_umo,
            push_time=push_time,
            languages=languages,
            push_count=push_count,
        )

    async def _schedule_for(self, session_umo: str) -> None:
        """为单个会话注册每日 cron 任务。"""
        cron_manager = self.ctx.cron_manager
        if cron_manager is None:
            raise RuntimeError("cron manager is not available")

        push_time = (
            self._normalize_time(str(self.config.get("push_time", DEFAULT_PUSH_TIME)))
            or DEFAULT_PUSH_TIME
        )
        hour, minute = self._parse_time_to_hm(push_time)
        cron_expression = f"{minute} {hour} * * *"
        cron_job = await cron_manager.add_basic_job(
            name=f"{PLUGIN_NAME}:{session_umo}",
            cron_expression=cron_expression,
            handler=self._cron_push_handler,
            payload={"session_umo": session_umo},
            description="Daily repo of today push",
            enabled=True,
            persistent=False,
        )
        self._job_ids.append(cron_job.job_id)

    async def _cron_push_handler(self, session_umo: str) -> None:
        """Cron 回调：按当前配置生成并主动发送推送消息。"""
        if not bool(self.config.get("enabled", False)):
            return
        runtime_config = self._build_runtime_config(session_umo)
        try:
            messages = await self._build_push_messages(runtime_config)
        except Exception:
            logger.exception(
                "[%s] failed to build push messages for %s", PLUGIN_NAME, session_umo
            )
            return
        for message in messages:
            try:
                await self.ctx.send_message(
                    session_umo, MessageChain().message(message)
                )
            except Exception:
                logger.exception(
                    "[%s] failed to send message to %s", PLUGIN_NAME, session_umo
                )
        await self._maybe_append_history(session_umo, messages)

    async def _build_push_messages(self, config: PushConfig) -> list[str]:
        """根据配置构建最终推送消息列表。

        约定：
        1. opening 独立为一条消息；
        2. 每种语言（或 all languages）独立为一条消息。
        """
        languages = config.languages or []
        max_results = max(1, min(config.push_count, MAX_RESULTS_LIMIT))
        opening_line = await self._generate_persona_opening_line(
            session_umo=config.session_umo,
        )
        messages: list[str] = []
        if opening_line:
            messages.append(f"🗣️ {opening_line}")

        if not languages:
            repos = await self._fetch_trending_repos(
                self._build_trending_url(""),
                max_results,
            )
            repos = await self._enhance_repos_with_ai(
                repos=repos,
                session_umo=config.session_umo,
            )
            messages.append(self._format_repos_message("all languages", repos))
            return messages

        for language in languages:
            try:
                repos = await self._fetch_trending_repos(
                    self._build_trending_url(language),
                    max_results,
                )
                repos = await self._enhance_repos_with_ai(
                    repos=repos,
                    session_umo=config.session_umo,
                )
            except Exception as exc:
                messages.append(f"## {language}\nFailed to fetch: {exc}")
                continue
            messages.append(self._format_repos_message(language, repos))
        return messages

    def _format_repos_message(self, language: str, repos: list[RepoItem]) -> str:
        """将仓库列表格式化为可读文本块。"""
        header = f"## GitHub Trending (daily) - {language}"
        if not repos:
            return f"{header}\nNo trending repositories found."

        lines = [header]
        for idx, repo in enumerate(repos, 1):
            lines.append(self._format_repo_markdown_item(idx, repo))
        return "\n\n".join(lines)

    def _format_repo_markdown_item(self, idx: int, repo: RepoItem) -> str:
        """将单个仓库格式化为 Markdown 风格文本。"""
        desc = repo.description if repo.description else "No description."
        return (
            f"{idx}. **{repo.full_name}**\n"
            f"> {desc}\n"
            f"> 🧑‍💻 **Lang:** {repo.language} | ⭐ **Stars:** {repo.stars} | "
            f"🍴 **Forks:** {repo.forks} | 🔥 **Today:** {repo.stars_today}\n"
            f"> 🔗 {repo.url}"
        )

    async def _enhance_repos_with_ai(
        self,
        repos: list[RepoItem],
        session_umo: str,
    ) -> list[RepoItem]:
        """使用 LLM 为仓库生成简短中文描述，并替换原始描述。"""
        if not repos:
            return repos

        persona_prompt = await self._resolve_active_persona_prompt(session_umo)
        tasks = [
            self._generate_ai_description(
                repo=repo,
                session_umo=session_umo,
                persona_prompt=persona_prompt,
            )
            for repo in repos
        ]
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
        self,
        repo: RepoItem,
        session_umo: str,
        persona_prompt: str = "",
    ) -> str:
        """调用当前会话 LLM 为单个仓库生成一句话描述。"""
        try:
            provider_id = await self.ctx.get_current_chat_provider_id(umo=session_umo)
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
            llm_resp = await self.ctx.llm_generate(
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

    async def _generate_persona_opening_line(self, session_umo: str) -> str:
        """基于当前启用人格生成本次运行的开场语。"""
        try:
            provider_id = await self.ctx.get_current_chat_provider_id(umo=session_umo)
            persona_prompt = await self._resolve_active_persona_prompt(session_umo)
            if not persona_prompt:
                return ""
            llm_resp = await self.ctx.llm_generate(
                chat_provider_id=provider_id,
                prompt=(
                    "你将要进行repo of today的推荐，写一句本次推荐开始前的中文开场白（8-20字）。"
                    "要求自然、简短，不要使用 markdown，不要加引号。"
                ),
                system_prompt=persona_prompt,
                temperature=1,
                max_tokens=60,
            )
            text = re.sub(r"\s+", " ", (llm_resp.completion_text or "").strip())
            return text
        except Exception as exc:
            logger.warning(
                "[%s] failed to generate persona opening line: %s",
                PLUGIN_NAME,
                exc,
            )
            return ""

    async def _resolve_active_persona_prompt(self, session_umo: str) -> str:
        """解析当前会话启用人格并返回 system_prompt。"""
        try:
            curr_cid = await self.ctx.conversation_manager.get_curr_conversation_id(
                session_umo
            )
            conversation_persona_id = None
            if curr_cid:
                conv = await self.ctx.conversation_manager.get_conversation(
                    unified_msg_origin=session_umo,
                    conversation_id=curr_cid,
                )
                if conv:
                    conversation_persona_id = conv.persona_id

            provider_settings = self.ctx.get_config(umo=session_umo).get(
                "provider_settings",
                {},
            )
            platform_name = session_umo.split(":", 1)[0] if ":" in session_umo else ""
            _, persona, _, _ = await self.ctx.persona_manager.resolve_selected_persona(
                umo=session_umo,
                conversation_persona_id=conversation_persona_id,
                platform_name=platform_name,
                provider_settings=provider_settings,
            )
            if persona and persona.get("prompt"):
                return str(persona["prompt"])
        except Exception as exc:
            logger.warning(
                "[%s] failed to resolve active persona prompt: %s",
                PLUGIN_NAME,
                exc,
            )
        return ""

    async def _typing_heartbeat(self, event: AstrMessageEvent) -> None:
        """在长任务执行期间周期性发送 typing 状态。"""
        while True:
            await event.send_typing()
            await asyncio.sleep(4)

    async def _maybe_append_history(
        self,
        session_umo: str,
        messages: list[str],
    ) -> None:
        """若开启 append_to_message_chain，把推送内容写入当前会话对话历史。"""
        if not bool(self.config.get("append_to_message_chain", False)):
            return
        if not messages:
            return
        try:
            conv_mgr = self.ctx.conversation_manager
            curr_cid = await conv_mgr.get_curr_conversation_id(session_umo)
            if not curr_cid:
                return
            conv = await conv_mgr.get_conversation(
                unified_msg_origin=session_umo,
                conversation_id=curr_cid,
            )
            if not conv:
                return
            history: list[dict] = []
            if conv.history:
                try:
                    parsed = json.loads(conv.history)
                    if isinstance(parsed, list):
                        history = parsed
                except (TypeError, ValueError):
                    history = []
            history.append({"role": "assistant", "content": "\n\n".join(messages)})
            await conv_mgr.update_conversation(
                unified_msg_origin=session_umo,
                conversation_id=curr_cid,
                history=history,
            )
        except Exception as exc:
            logger.warning(
                "[%s] failed to append history for %s: %s",
                PLUGIN_NAME,
                session_umo,
                exc,
            )

    async def _delete_job(self, job_id: str) -> None:
        """删除指定 Cron 任务。"""
        if not job_id:
            return
        cron_manager = self.ctx.cron_manager
        if cron_manager is None:
            return
        try:
            await cron_manager.delete_job(job_id)
        except Exception:
            logger.warning("[%s] failed to delete cron job %s", PLUGIN_NAME, job_id)

    def _normalize_language(self, language: str) -> str:
        """标准化语言参数并过滤非法字符。"""
        value = language.strip().lower()
        return re.sub(r"[^a-z0-9_+#-]", "", value)

    def _normalize_time(self, value: str) -> str | None:
        """校验并标准化时间字符串为 HH:MM。"""
        match = re.fullmatch(r"\s*([01]?\d|2[0-3]):([0-5]\d)\s*", value)
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2))
        return f"{hour:02d}:{minute:02d}"

    def _parse_time_to_hm(self, value: str) -> tuple[int, int]:
        """将 HH:MM 解析为小时和分钟。"""
        normalized = self._normalize_time(value)
        if not normalized:
            raise ValueError("invalid time format")
        hour_str, minute_str = normalized.split(":", 1)
        return int(hour_str), int(minute_str)

    def _build_trending_url(self, language: str) -> str:
        """构建 GitHub Trending 请求地址。"""
        if language:
            return f"{TRENDING_BASE_URL}/{quote(language)}?since=daily"
        return f"{TRENDING_BASE_URL}?since=daily"

    async def _fetch_trending_repos(
        self,
        url: str,
        max_results: int,
    ) -> list[RepoItem]:
        """抓取 Trending 页面并解析仓库条目。"""
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(
            timeout=timeout,
            trust_env=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        ) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                html = await response.text()
        return self._parse_trending_html(html, max_results)

    def _parse_trending_html(self, html: str, max_results: int) -> list[RepoItem]:
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

            stars = self._extract_stat(article, "/stargazers")
            forks = self._extract_stat(article, "/forks")
            stars_today = self._extract_today_stars(article)

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

    def _extract_stat(self, article: Tag, href_suffix: str) -> str:
        """提取仓库统计字段（Star/Fork）。"""
        tag = article.select_one(f'a[href$="{href_suffix}"]')
        if not tag:
            return "N/A"
        return re.sub(r"\s+", "", tag.get_text(strip=True))

    def _extract_today_stars(self, article: Tag) -> str:
        """提取今日新增 Star 字段。"""
        tag = article.select_one("span.d-inline-block.float-sm-right")
        if not tag:
            return "N/A"
        text = re.sub(r"\s+", " ", tag.get_text(strip=True)).lower()
        return text.replace("stars today", "").replace("star today", "").strip() or text
