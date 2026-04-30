# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

本仓库是一个 AstrBot 插件，用 uv 管理依赖，Python ≥ 3.10。

## 常用命令

```bash
# 跑回归测试（含真实 GitHub 调用，约 9s）
uv run --group dev pytest -q
uv run --group dev pytest tests/test_commits_live.py -v        # 单文件
uv run --group dev pytest tests/test_config.py::TestTrackedRepos -v  # 单类
uv run --group dev pytest -k "tracked_repos and parses" -v     # 关键字筛选

# Lint / Format
uv run --group dev ruff check main.py src/ tests/ scripts/
uv run --group dev ruff format --check main.py src/ tests/ scripts/

# 完整 pre-commit / pre-push hook（手动触发）
uv tool run pre-commit run --all-files
uv tool run pre-commit run --hook-stage pre-push --all-files

# 安装本地 git hook（一次即可）
uv tool run pre-commit install --install-hooks

# 重新生成 _conf_schema.json 中的 languages.options
uv run --group scripts python scripts/update_languages.py
```

`pre-commit` 阶段跑 ruff + pyupgrade，`pre-push` 阶段跑 `pytest -q`。push 前会真实联网；GitHub 改 HTML / JSON 形态时立刻报警。

## 架构边界

**强约束：`main.py` 仅作 AstrBot 适配层。** 仅允许出现：

- `Star` 子类、`__init__` / `initialize` / `terminate`、`_job_lock` / `_job_ids`
- `@filter.command(...)` handler
- cron 注册（`ctx.cron_manager.add_basic_job`）与 `ctx.send_message` 调用

**不允许**在 `main.py` 出现：HTTP 抓取、HTML/JSON 解析、LLM 调用、文本拼装、配置归一化、状态文件 IO。这些都属于 `src/` 包，每个 handler 体内基本是 `await pipeline.run_xxx(...)` + `event.plain_result(...)`。

**导入风格强约束：`main.py` 必须用相对导入**（`from .src.xxx import ...`）。AstrBot 把插件目录整个当作一个包加载（`data.plugins.astrbot_plugin_repo_of_today`），`from src import ...` 形式的绝对导入会因为找不到顶层 `src` 模块而启动失败。`src/` 内部模块互相之间也已经全部使用相对导入。

## src/ 业务包结构

`src/` 是这个插件的真实逻辑所在。模块按职责划分：

- `models.py` — 全部 dataclass（`RepoItem`、`PushConfig`、`RepoCommit`、`RepoUpdate`），无依赖。
- `config.py` — 配置读取与归一化。`tracked_repos()` 用宽松正则解析多种 GitHub URL 形式（主页 / `.git` / `/tree/branch` / SSH）。
- `github/trending.py` — Trending HTML 抓取与 BeautifulSoup 解析。
- `github/commits.py` — 通过 GitHub REST API（匿名，60/h）抓取仓库默认分支提交。`fetch_commits_since` 用 `?since={ISO}` 拿过去 24h，硬上限 50 条防 prompt 爆炸；不存在 / rate-limited 仓库写入 `RepoUpdate.error` 而不抛异常。
- `llm.py` — 人格解析 + 三种 LLM 调用：trending 单仓库描述、开场白、commits 批量总结。所有函数接受 `ctx: Context`、不持有 `self`，便于复用与测试。
- `formatting.py` — 纯字符串拼装。
- `history.py` — `append_to_message_chain=true` 时把消息追加到当前会话历史。
- `typing_heartbeat.py` — `typing_indicator(event)` async context manager，命令执行期间持续发送 typing。
- `pipeline.py` — **业务流编排，所有"共享抓取 → 按会话 LLM 增强 → 返回消息列表"逻辑都在这里。**

## 关键数据流

每日 cron 触发 → `Main._cron_broadcast_handler`：

1. 调 `pipeline.run_trending(ctx, config, sessions)` —— 每语言抓一次，再按会话独立做人格 + LLM 增强。
2. 调 `pipeline.run_track(ctx, config, sessions)` —— 共享一次 commits 抓取（24h 窗口、`asyncio.Semaphore(LLM_CONCURRENCY=3)` 限并发），按会话独立总结。
3. 对每个 session 顺序 `ctx.send_message` + `history.append_history`。

`/repo_today` / `/repo_track` 两个命令分别走 `pipeline.run_repo_today_once` / `run_repo_track_once`，单会话路径，**无副作用**（与 cron 走同一份纯函数）。

设计要点：
- 共享抓取 + 按会话增强：N 个 target_sessions 只触发 1 次 GitHub 抓取，但 N 次 LLM 调用（人格不同）。
- 失败隔离：单个仓库 / 单个会话失败仅影响自身，不会拖垮整次 cron。
- 24h 窗口而非 SHA 去重：插件不维护任何状态文件；首次运行无 baseline 问题，命令重复触发也无副作用。
- `LLM_CONCURRENCY = 3`（`src/__init__.py`）是抓取与 LLM 通用的并发闸门，防止压垮 provider 与 GitHub 匿名速率。

## 测试哲学

`tests/` 不使用 fixture 兜底解析逻辑：

- `test_trending_live.py` / `test_commits_live.py` 直接打真实 GitHub，断言字段齐全 + 错误兜底 + 截断生效。GitHub 改外部形态时第一时间挂掉。
- `test_config.py` / `test_formatting.py` 是纯函数（URL 解析、消息渲染），无网络。
- `tests/conftest.py` 自动 stub `astrbot.api`，无需 AstrBot 运行时也能 import `src.*`。

如果新增对 GitHub 数据形态的解析逻辑，**写真实联网测试**，不要造 fixture。

## 配置与发布

- 用户可见配置定义在 `_conf_schema.json`（AstrBot WebUI 解析）。新增字段时同步更新 README 表格。
- 版本号同时维护两处：`metadata.yaml`（AstrBot 显示）与 `pyproject.toml`（uv 项目）。
- `requirements.txt` 是 AstrBot 加载插件时安装的最小集（仅 `beautifulsoup4`，aiohttp 由 AstrBot 自带）。`pyproject.toml` 的 `[project.dependencies]` 是本地开发需要的全集。两者保持一致或 `requirements.txt` 是子集。
