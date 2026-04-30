"""文本拼装。"""

from __future__ import annotations

from .models import RepoItem, RepoUpdate


def format_trending_message(language: str, repos: list[RepoItem]) -> str:
    """将 trending 仓库列表格式化为可读文本块。"""
    header = f"## GitHub Trending (daily) - {language}"
    if not repos:
        return f"{header}\nNo trending repositories found."
    lines = [header]
    for idx, repo in enumerate(repos, 1):
        lines.append(format_trending_item(idx, repo))
    return "\n\n".join(lines)


def format_trending_item(idx: int, repo: RepoItem) -> str:
    """单条 trending 仓库的 markdown 行。"""
    desc = repo.description if repo.description else "No description."
    return (
        f"{idx}. **{repo.full_name}**\n"
        f"> {desc}\n"
        f"> 🧑‍💻 **Lang:** {repo.language} | ⭐ **Stars:** {repo.stars} | "
        f"🍴 **Forks:** {repo.forks} | 🔥 **Today:** {repo.stars_today}\n"
        f"> 🔗 {repo.url}"
    )


def format_repo_update_message(
    update: RepoUpdate,
    overview: str,
    explanations: list[str],
    show_count: int,
) -> str:
    """单个追踪仓库的 24h 提交消息：概述 + 每条 commit 的中文说明 + 裸 URL。

    - explanations 与 update.commits[:show_count] 对齐，缺位调用方需保证已用原 message 兜底。
    - 不使用 markdown 链接语法（`[]()`）：Telegram 等 IM 不渲染，纯 URL 会被自动识别。
    """
    if update.error:
        return f"📦 {update.full_name} · 抓取失败：{update.error}"
    total = len(update.commits)
    if not total:
        return ""

    header = f"📦 {update.full_name} · 过去 24h · {total} 条"
    parts: list[str] = [header]
    if overview:
        parts.append(overview)

    visible = min(total, show_count)
    blocks: list[str] = []
    for i in range(visible):
        commit = update.commits[i]
        explanation = (
            explanations[i]
            if i < len(explanations) and explanations[i]
            else commit.message
        )
        blocks.append(f"• {explanation}\n  {commit.url}")
    if blocks:
        parts.append("\n\n".join(blocks))

    footer_lines: list[str] = []
    remaining = total - visible
    if remaining > 0:
        footer_lines.append(f"_另有 {remaining} 条更新…_")
    if update.truncated:
        footer_lines.append("_显示最近 50 条，超出部分已截断_")
    footer_lines.append(f"🔗 https://github.com/{update.full_name}/commits")
    parts.append("\n".join(footer_lines))

    return "\n\n".join(parts)
