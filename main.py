"""Repo Of Today 插件入口。

仅作 AstrBot 适配层：Star 子类、命令注册、cron 注册、生命周期管理。
所有业务逻辑由 `src/` 包提供。
"""

from __future__ import annotations

import asyncio
from typing import cast

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

from .src import PLUGIN_NAME
from .src.config import (
    append_history_enabled,
    parse_time_to_hm,
    push_enabled,
    target_sessions,
)
from .src.history import append_history
from .src.pipeline import (
    run_repo_today_once,
    run_repo_track_once,
    run_track,
    run_trending,
)
from .src.typing_heartbeat import typing_indicator


class Main(Star):
    """Repo Of Today 插件主类（仅作框架适配）。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = config
        self._job_lock = asyncio.Lock()
        self._job_ids: list[str] = []

    @property
    def ctx(self) -> Context:
        return cast(Context, self.context)

    async def initialize(self) -> None:
        """插件加载时按配置注册一个全局定时推送任务。"""
        if not push_enabled(self.config):
            return
        if not target_sessions(self.config):
            return
        async with self._job_lock:
            try:
                await self._schedule_global_job()
            except Exception:
                logger.exception("[%s] failed to schedule global cron job", PLUGIN_NAME)

    async def terminate(self) -> None:
        """插件卸载时清理已注册任务，避免重复触发。"""
        async with self._job_lock:
            for job_id in list(self._job_ids):
                await self._delete_job(job_id)
            self._job_ids.clear()

    @filter.command("repo_today")
    async def repo_today(self, event: AstrMessageEvent):
        """手动查询 GitHub 当日热门仓库。"""
        async with typing_indicator(event):
            try:
                messages = await run_repo_today_once(
                    self.ctx, self.config, event.unified_msg_origin
                )
            except Exception as exc:
                logger.exception("[%s] failed to fetch trending repos", PLUGIN_NAME)
                yield event.plain_result(
                    f"Failed to fetch trending repositories: {exc}"
                )
                return
            for msg in messages:
                yield event.plain_result(msg)
            await append_history(
                self.ctx,
                event.unified_msg_origin,
                messages,
                enabled=append_history_enabled(self.config),
            )

    @filter.command("repo_track")
    async def repo_track(self, event: AstrMessageEvent):
        """手动拉取已配置追踪仓库过去 24 小时的提交并 LLM 总结。"""
        async with typing_indicator(event):
            try:
                messages = await run_repo_track_once(
                    self.ctx, self.config, event.unified_msg_origin
                )
            except Exception as exc:
                logger.exception("[%s] failed to fetch tracked commits", PLUGIN_NAME)
                yield event.plain_result(f"Failed to fetch tracked repos: {exc}")
                return
            if not messages:
                yield event.plain_result("过去 24 小时没有追踪仓库的新提交。")
                return
            for msg in messages:
                yield event.plain_result(msg)
            await append_history(
                self.ctx,
                event.unified_msg_origin,
                messages,
                enabled=append_history_enabled(self.config),
            )

    async def _schedule_global_job(self) -> None:
        """注册一个全局每日 cron 任务。"""
        cron_manager = self.ctx.cron_manager
        if cron_manager is None:
            raise RuntimeError("cron manager is not available")

        # add_basic_job(persistent=False) 仍写一行 DB 且无自动清理路径；
        # 上次 terminate 没正常跑（崩溃 / kill -9 / 旧版本）会留 orphan 行——
        # 触发时找不到 handler 反复抛 RuntimeError。先按 name 清干净再新建。
        try:
            existing = await cron_manager.list_jobs("basic")
        except Exception:
            logger.exception("[%s] failed to list cron jobs", PLUGIN_NAME)
            existing = []
        for job in existing:
            if getattr(job, "name", None) != PLUGIN_NAME:
                continue
            try:
                await cron_manager.delete_job(job.job_id)
                logger.info(
                    "[%s] cleaned stale cron job: %s", PLUGIN_NAME, job.job_id
                )
            except Exception:
                logger.warning(
                    "[%s] failed to delete stale cron job %s",
                    PLUGIN_NAME,
                    job.job_id,
                )

        push_time = self.config.get("push_time", "09:00")
        try:
            hour, minute = parse_time_to_hm(str(push_time))
        except ValueError:
            hour, minute = 9, 0
        cron_expression = f"{minute} {hour} * * *"
        cron_job = await cron_manager.add_basic_job(
            name=PLUGIN_NAME,
            cron_expression=cron_expression,
            handler=self._cron_broadcast_handler,
            description="Daily repo of today broadcast",
            enabled=True,
            persistent=False,
        )
        self._job_ids.append(cron_job.job_id)

    async def _cron_broadcast_handler(self) -> None:
        """Cron 回调：先广播 trending，再广播追踪仓库的 24h 提交总结。"""
        if not push_enabled(self.config):
            return
        sessions = target_sessions(self.config)
        if not sessions:
            return

        history_enabled = append_history_enabled(self.config)

        trending_messages = await run_trending(self.ctx, self.config, sessions)
        for umo, messages in trending_messages.items():
            await self._broadcast_to_session(umo, messages, history_enabled)

        track_messages = await run_track(self.ctx, self.config, sessions)
        for umo, messages in track_messages.items():
            await self._broadcast_to_session(umo, messages, history_enabled)

    async def _broadcast_to_session(
        self,
        umo: str,
        messages: list[str],
        history_enabled: bool,
    ) -> None:
        """把消息列表发送到单个会话，并根据配置写入历史。"""
        if not messages:
            return
        for message in messages:
            try:
                await self.ctx.send_message(umo, MessageChain().message(message))
            except Exception:
                logger.exception("[%s] failed to send message to %s", PLUGIN_NAME, umo)
        await append_history(self.ctx, umo, messages, enabled=history_enabled)

    async def _delete_job(self, job_id: str) -> None:
        if not job_id:
            return
        cron_manager = self.ctx.cron_manager
        if cron_manager is None:
            return
        try:
            await cron_manager.delete_job(job_id)
        except Exception:
            logger.warning("[%s] failed to delete cron job %s", PLUGIN_NAME, job_id)
