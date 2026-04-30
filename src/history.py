"""会话历史追加。"""

from __future__ import annotations

import json

from astrbot.api import logger
from astrbot.api.star import Context

from . import PLUGIN_NAME


async def append_history(
    ctx: Context,
    session_umo: str,
    messages: list[str],
    enabled: bool,
) -> None:
    """若 enabled，把推送内容以 assistant 角色写入会话当前对话历史。"""
    if not enabled or not messages:
        return
    try:
        conv_mgr = ctx.conversation_manager
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
