from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup, Tag

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

PLUGIN_NAME = "repo_of_today"
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_LIMIT = 10
TRENDING_BASE_URL = "https://github.com/trending"
DEFAULT_PUSH_TIME = "09:00"
SUBSCRIPTION_INDEX_KEY = "repo_today_subscriptions"
SUBSCRIPTION_KEY_PREFIX = "repo_today_subscription"


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
    """单个会话的推送配置。"""

    session_umo: str
    """会话唯一标识（UMO）。"""
    push_time: str = DEFAULT_PUSH_TIME
    """每日推送时间，格式为 HH:MM。"""
    languages: list[str] | None = None
    """关注语言列表；空列表表示全语言。"""
    push_count: int = DEFAULT_MAX_RESULTS
    """每种语言推送的仓库数量。"""
    enabled: bool = False
    """是否启用定时推送。"""
    job_id: str = ""
    """当前绑定的 Cron 任务 ID。"""

    def to_dict(self) -> dict:
        """将配置对象序列化为可持久化的字典。"""
        return {
            "session_umo": self.session_umo,
            "push_time": self.push_time,
            "languages": self.languages or [],
            "push_count": self.push_count,
            "enabled": self.enabled,
            "job_id": self.job_id,
        }

    @staticmethod
    def from_dict(data: dict, session_umo: str) -> PushConfig:
        """从字典恢复配置对象。"""
        push_time = str(data.get("push_time", DEFAULT_PUSH_TIME))
        raw_languages = data.get("languages", [])
        languages = raw_languages if isinstance(raw_languages, list) else []
        push_count = int(data.get("push_count", DEFAULT_MAX_RESULTS))
        enabled = bool(data.get("enabled", False))
        job_id = str(data.get("job_id", ""))
        return PushConfig(
            session_umo=session_umo,
            push_time=push_time,
            languages=languages,
            push_count=push_count,
            enabled=enabled,
            job_id=job_id,
        )


class Main(Star):
    """Repo Of Today 插件主类。

    功能包含：
    1. 手动查询 GitHub 每日 Trending。
    2. 按会话配置定时推送时间、语言数组和推送数量。
    3. 在插件重载后恢复已启用的定时任务。
    """

    def __init__(self, context: Context) -> None:
        """初始化插件上下文和任务锁。"""
        super().__init__(context)
        self.context = context
        self._job_lock = asyncio.Lock()

    @property
    def ctx(self) -> Context:
        """返回具备完整类型提示的 Context。"""
        return cast(Context, self.context)

    async def initialize(self) -> None:
        """插件加载时恢复历史定时任务。"""
        await self._restore_jobs()

    async def terminate(self) -> None:
        """插件卸载时清理已注册任务，避免重复触发。"""
        async with self._job_lock:
            for session_umo in await self._list_subscriptions():
                config = await self._load_subscription(session_umo)
                if config.job_id:
                    await self._delete_job(config.job_id)
                    config.job_id = ""
                    await self._save_subscription(config)

    @filter.command("repo_today")
    async def repo_today(
        self,
        event: AstrMessageEvent,
        language: str = "",
        count: int = DEFAULT_MAX_RESULTS,
    ):
        """手动查询 GitHub 当日热门仓库。

        用法：
        /repo_today
        /repo_today python
        /repo_today python 3
        """
        session_config = await self._load_subscription(event.unified_msg_origin)
        cmd_tokens = self.parse_commands(event.message_str)
        args_len = max(0, cmd_tokens.len - 1)

        runtime_languages = session_config.languages or []
        runtime_count = session_config.push_count

        if args_len >= 1:
            language_arg = self._normalize_language(language)
            if language.strip().lower() in {"all", "*"}:
                runtime_languages = []
            elif language_arg:
                runtime_languages = [language_arg]
        if args_len >= 2:
            runtime_count = max(1, min(count, MAX_RESULTS_LIMIT))

        runtime_config = PushConfig(
            session_umo=event.unified_msg_origin,
            push_time=session_config.push_time,
            languages=runtime_languages,
            push_count=runtime_count,
            enabled=session_config.enabled,
            job_id=session_config.job_id,
        )

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
        finally:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

    @filter.command("repo_today_time")
    async def repo_today_time(
        self,
        event: AstrMessageEvent,
        push_time: str = "",
    ):
        """设置每日推送时间。

        示例：`/repo_today_time 09:00`
        """
        normalized_time = self._normalize_time(push_time)
        if not normalized_time:
            yield event.plain_result(
                "Invalid time format. Use HH:MM, for example 09:00."
            )
            return

        async with self._job_lock:
            config = await self._load_subscription(event.unified_msg_origin)
            config.push_time = normalized_time
            await self._save_subscription(config)
            if config.enabled:
                await self._reschedule(config)

        yield event.plain_result(f"Repo push time set to {normalized_time}.")

    @filter.command("repo_today_langs")
    async def repo_today_langs(
        self,
        event: AstrMessageEvent,
        languages_csv: str = "",
    ):
        """设置关注语言数组。

        示例：`/repo_today_langs python,go,rust` 或 `/repo_today_langs all`
        """
        languages = self._parse_languages(languages_csv)
        if languages is None:
            yield event.plain_result(
                "Invalid languages. Use comma-separated values, e.g. python,go,rust, or 'all'."
            )
            return

        async with self._job_lock:
            config = await self._load_subscription(event.unified_msg_origin)
            config.languages = languages
            await self._save_subscription(config)

        if not languages:
            yield event.plain_result("Subscribed languages updated: all languages.")
            return
        yield event.plain_result(
            "Subscribed languages updated: " + ", ".join(languages) + "."
        )

    @filter.command("repo_today_count")
    async def repo_today_count(
        self,
        event: AstrMessageEvent,
        push_count: int = DEFAULT_MAX_RESULTS,
    ):
        """设置每种语言推送数量。

        示例：`/repo_today_count 5`
        """
        normalized_count = max(1, min(push_count, MAX_RESULTS_LIMIT))
        async with self._job_lock:
            config = await self._load_subscription(event.unified_msg_origin)
            config.push_count = normalized_count
            await self._save_subscription(config)
        yield event.plain_result(f"Push count set to {normalized_count}.")

    @filter.command("repo_today_push")
    async def repo_today_push(
        self,
        event: AstrMessageEvent,
        status: str = "",
    ):
        """开启或关闭定时推送。

        示例：`/repo_today_push on`
        """
        action = status.strip().lower()
        if action not in {"on", "off"}:
            yield event.plain_result("Usage: /repo_today_push on|off")
            return

        async with self._job_lock:
            config = await self._load_subscription(event.unified_msg_origin)
            config.enabled = action == "on"
            if config.enabled:
                await self._reschedule(config)
            else:
                if config.job_id:
                    await self._delete_job(config.job_id)
                    config.job_id = ""
                await self._save_subscription(config)

        state = "enabled" if action == "on" else "disabled"
        yield event.plain_result(f"Scheduled repo push is now {state}.")

    @filter.command("repo_today_config")
    async def repo_today_config(self, event: AstrMessageEvent):
        """查看当前会话的推送配置。"""
        config = await self._load_subscription(event.unified_msg_origin)
        langs = ", ".join(config.languages or []) if config.languages else "all"
        state = "on" if config.enabled else "off"
        yield event.plain_result(
            "Repo of Today config:\n"
            f"- push: {state}\n"
            f"- time: {config.push_time}\n"
            f"- languages: {langs}\n"
            f"- count: {config.push_count}"
        )

    async def _restore_jobs(self) -> None:
        """根据持久化配置恢复已启用的 Cron 任务。"""
        async with self._job_lock:
            for session_umo in await self._list_subscriptions():
                config = await self._load_subscription(session_umo)
                if not config.enabled:
                    continue
                await self._reschedule(config)

    async def _reschedule(self, config: PushConfig) -> None:
        """按配置重新创建每日定时任务。"""
        if config.job_id:
            await self._delete_job(config.job_id)

        cron_manager = self.ctx.cron_manager
        if cron_manager is None:
            raise RuntimeError("cron manager is not available")

        hour, minute = self._parse_time_to_hm(config.push_time)
        cron_expression = f"{minute} {hour} * * *"
        cron_job = await cron_manager.add_basic_job(
            name=f"{PLUGIN_NAME}:{config.session_umo}",
            cron_expression=cron_expression,
            handler=self._cron_push_handler,
            payload={"session_umo": config.session_umo},
            description="Daily repo of today push",
            enabled=True,
            persistent=False,
        )
        config.job_id = cron_job.job_id
        await self._save_subscription(config)

    async def _cron_push_handler(self, session_umo: str) -> None:
        """Cron 回调：生成并主动发送推送消息。"""
        config = await self._load_subscription(session_umo)
        if not config.enabled:
            return
        messages = await self._build_push_messages(config)
        for message in messages:
            await self.ctx.send_message(session_umo, MessageChain().message(message))

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

    async def _load_subscription(self, session_umo: str) -> PushConfig:
        """读取并规范化某个会话的配置。"""
        key = self._subscription_key(session_umo)
        data = await self.get_kv_data(key, {})
        if not isinstance(data, dict):
            data = {}
        config = PushConfig.from_dict(data, session_umo)
        config.push_time = self._normalize_time(config.push_time) or DEFAULT_PUSH_TIME
        config.push_count = max(1, min(config.push_count, MAX_RESULTS_LIMIT))
        config.languages = self._parse_languages(",".join(config.languages or [])) or []
        return config

    async def _save_subscription(self, config: PushConfig) -> None:
        """保存会话配置并登记到订阅索引。"""
        await self.put_kv_data(
            self._subscription_key(config.session_umo), config.to_dict()
        )
        await self._register_subscription(config.session_umo)

    async def _register_subscription(self, session_umo: str) -> None:
        """将会话加入订阅索引，便于插件重启后恢复任务。"""
        current = await self._list_subscriptions()
        if session_umo in current:
            return
        current.append(session_umo)
        await self.put_kv_data(SUBSCRIPTION_INDEX_KEY, current)

    async def _list_subscriptions(self) -> list[str]:
        """获取所有已登记的会话 UMO 列表。"""
        values = await self.get_kv_data(SUBSCRIPTION_INDEX_KEY, [])
        if not isinstance(values, list):
            return []
        return [str(v) for v in values if isinstance(v, str) and v]

    def _subscription_key(self, session_umo: str) -> str:
        """生成会话配置在 KV 中的键名。"""
        return f"{SUBSCRIPTION_KEY_PREFIX}:{session_umo}"

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

    def _parse_languages(self, languages_csv: str) -> list[str] | None:
        """解析语言数组参数。

        返回空列表表示全语言，返回 None 表示参数非法。
        """
        raw = languages_csv.strip().lower()
        if not raw or raw in {"all", "*"}:
            return []
        parts = re.split(r"[,，\s]+", raw)
        normalized = [self._normalize_language(part) for part in parts if part.strip()]
        normalized = [part for part in normalized if part]
        if not normalized:
            return None
        deduped: list[str] = []
        seen: set[str] = set()
        for lang in normalized:
            if lang in seen:
                continue
            seen.add(lang)
            deduped.append(lang)
        return deduped

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
        """提取“今日新增 Star”字段。"""
        tag = article.select_one("span.d-inline-block.float-sm-right")
        if not tag:
            return "N/A"
        text = re.sub(r"\s+", " ", tag.get_text(strip=True)).lower()
        return text.replace("stars today", "").replace("star today", "").strip() or text
