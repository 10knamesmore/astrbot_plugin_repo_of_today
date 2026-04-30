"""数据类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import DEFAULT_PUSH_COUNT, DEFAULT_PUSH_TIME


@dataclass(slots=True)
class RepoItem:
    """GitHub 仓库条目（来自 Trending）。"""

    full_name: str
    description: str
    language: str
    stars: str
    forks: str
    stars_today: str
    url: str


@dataclass(slots=True)
class PushConfig:
    """单次运行的运行时配置。"""

    session_umo: str
    push_time: str = DEFAULT_PUSH_TIME
    languages: list[str] | None = None
    push_count: int = DEFAULT_PUSH_COUNT
    tracked_repos: list[tuple[str, str]] = field(default_factory=list)
    track_show_count: int = 5


@dataclass(slots=True)
class RepoCommit:
    """单条 commit 信息。"""

    sha: str
    short_sha: str
    message: str
    author: str
    committed_at: str
    url: str


@dataclass(slots=True)
class RepoUpdate:
    """某仓库在窗口内的提交更新。"""

    full_name: str
    commits: list[RepoCommit]
    truncated: bool = False
    error: str | None = None
