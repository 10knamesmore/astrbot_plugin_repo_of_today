"""Stub astrbot.api before importing the plugin modules.

允许在没有 AstrBot 运行时的 CI / pre-push 环境里直接 import `src.*`。
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "astrbot" not in sys.modules:
    _astrbot = types.ModuleType("astrbot")
    _astrbot_api = types.ModuleType("astrbot.api")
    _astrbot_api.logger = logging.getLogger("astrbot")  # type: ignore[attr-defined]
    _astrbot_api.AstrBotConfig = dict  # type: ignore[attr-defined]
    sys.modules["astrbot"] = _astrbot
    sys.modules["astrbot.api"] = _astrbot_api
