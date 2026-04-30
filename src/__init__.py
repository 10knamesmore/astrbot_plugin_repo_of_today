"""Repo Of Today 插件业务模块。

main.py 仅作为 AstrBot 适配层，全部业务逻辑在本包内组织。
"""

from __future__ import annotations

PLUGIN_NAME = "astrbot_plugin_repo_of_today"

DEFAULT_PUSH_TIME = "09:00"
DEFAULT_PUSH_COUNT = 5
MAX_RESULTS_LIMIT = 10

LLM_CONCURRENCY = 3

TRENDING_BASE_URL = "https://github.com/trending"
GITHUB_API_BASE = "https://api.github.com"

TRACK_HARD_CAP = 50
TRACK_WINDOW_HOURS = 24
TRACK_DEFAULT_SHOW_COUNT = 5
TRACK_SHOW_COUNT_MIN = 1
TRACK_SHOW_COUNT_MAX = 50

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
