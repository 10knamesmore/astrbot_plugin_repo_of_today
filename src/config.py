"""配置读取与归一化。"""

from __future__ import annotations

import re

from astrbot.api import AstrBotConfig, logger

from . import DEFAULT_PUSH_COUNT, DEFAULT_PUSH_TIME, MAX_RESULTS_LIMIT, PLUGIN_NAME
from .models import PushConfig

_TIME_RE = re.compile(r"\s*([01]?\d|2[0-3]):([0-5]\d)\s*")
_LANG_RE = re.compile(r"[^a-z0-9_+#-]")
_REPO_URL_RE = re.compile(
    r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?/?$",
    re.IGNORECASE,
)


def normalize_time(value: str) -> str | None:
    """校验并标准化时间字符串为 HH:MM。"""
    match = _TIME_RE.fullmatch(value)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    return f"{hour:02d}:{minute:02d}"


def parse_time_to_hm(value: str) -> tuple[int, int]:
    """将 HH:MM 解析为小时和分钟。"""
    normalized = normalize_time(value)
    if not normalized:
        raise ValueError("invalid time format")
    hour_str, minute_str = normalized.split(":", 1)
    return int(hour_str), int(minute_str)


def normalize_language(language: str) -> str:
    """标准化语言参数并过滤非法字符。"""
    return _LANG_RE.sub("", language.strip().lower())


def target_sessions(config: AstrBotConfig) -> list[str]:
    """从配置中读取规范化后的 UMO 列表。"""
    raw = config.get("target_sessions", []) or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]


def tracked_repos(config: AstrBotConfig) -> list[tuple[str, str]]:
    """读取并解析 tracked_repos 配置，返回 [(owner, repo), ...]。

    支持的输入：
    - https://github.com/owner/repo
    - https://github.com/owner/repo/
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo/tree/main
    - git@github.com:owner/repo.git
    非法项静默丢弃并 warning。
    """
    raw = config.get("tracked_repos", []) or []
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        match = _REPO_URL_RE.search(text)
        if not match:
            logger.warning("[%s] cannot parse tracked repo url: %r", PLUGIN_NAME, item)
            continue
        owner = match.group(1).strip()
        repo = match.group(2).strip()
        if not owner or not repo:
            continue
        out.append((owner, repo))
    return out


def build_runtime_config(config: AstrBotConfig, session_umo: str) -> PushConfig:
    """根据当前配置和 UMO 组装单次运行的 PushConfig。"""
    push_time = (
        normalize_time(str(config.get("push_time", DEFAULT_PUSH_TIME)))
        or DEFAULT_PUSH_TIME
    )

    raw_count = config.get("push_count", DEFAULT_PUSH_COUNT)
    try:
        push_count = int(raw_count)
    except (TypeError, ValueError):
        push_count = DEFAULT_PUSH_COUNT
    push_count = max(1, min(push_count, MAX_RESULTS_LIMIT))

    raw_languages = config.get("languages", []) or []
    if not isinstance(raw_languages, list):
        raw_languages = []
    languages = [
        normalize_language(str(item))
        for item in raw_languages
        if isinstance(item, str) and item.strip()
    ]
    languages = [lang for lang in languages if lang]

    return PushConfig(
        session_umo=session_umo,
        push_time=push_time,
        languages=languages,
        push_count=push_count,
        tracked_repos=tracked_repos(config),
    )


def append_history_enabled(config: AstrBotConfig) -> bool:
    return bool(config.get("append_to_message_chain", False))


def push_enabled(config: AstrBotConfig) -> bool:
    return bool(config.get("enabled", False))
