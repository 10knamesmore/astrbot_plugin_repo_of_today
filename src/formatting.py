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


def format_repo_update_message(update: RepoUpdate, summary: str) -> str:
    """单个追踪仓库的 24h 提交总结消息。"""
    header = f"## 📦 {update.full_name} 仓库更新"
    if update.error:
        return f"{header}\n抓取失败：{update.error}"
    if not update.commits:
        return ""

    body_lines = [header]
    if summary:
        body_lines.append(summary)

    body_lines.append(f"> 共 {len(update.commits)} 条新提交（过去 24 小时）")
    if update.truncated:
        body_lines.append("> _显示最近 50 条，超出部分已截断_")

    preview_count = min(3, len(update.commits))
    for c in update.commits[:preview_count]:
        body_lines.append(f"> - [`{c.short_sha}`]({c.url}) {c.message} —— {c.author}")

    body_lines.append(f"> 🔗 https://github.com/{update.full_name}/commits")
    return "\n".join(body_lines)
