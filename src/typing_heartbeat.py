"""命令处理期间向用户持续发送 typing 状态。"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from astrbot.api.event import AstrMessageEvent


async def _typing_heartbeat(event: AstrMessageEvent) -> None:
    while True:
        await event.send_typing()
        await asyncio.sleep(4)


@asynccontextmanager
async def typing_indicator(event: AstrMessageEvent) -> AsyncIterator[None]:
    """async with 包裹长任务，开始时启动 typing 心跳，退出时关闭并 stop_typing。"""
    task = asyncio.create_task(_typing_heartbeat(event))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(Exception):
            await event.stop_typing()
