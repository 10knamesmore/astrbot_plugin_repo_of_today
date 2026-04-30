# Changelog

## v0.3.0 — 2026-05-01

### Changed
- 追踪仓库消息格式重写：从「whole-repo 总览 + 3 条 markdown 链接的原始 commit message」改为「whole-repo 总览 + 每条 commit 的 LLM 中文说明 + 裸 commit URL」。Markdown 链接（`[]()`）被去除，Telegram 等 IM 直接拿到可点的裸 URL，不再有 `[](http...)` 字符串。
- 每条说明走一次批量 LLM 调用（编号列表解析），失败位置回落为对应 commit 的原 message。
- 头部从 `## 📦 owner/repo 仓库更新` 改为单行 `📦 owner/repo · 过去 24h · N 条`。

### Added
- 新增配置项 `track_show_count`（默认 5，clamp `[1, 50]`）：控制单条消息内展示多少条 commit 说明。超出部分汇总为 `_另有 N 条更新…_` + commits 页 URL。

## v0.2.1 — 2026-05-01

### Fixed
- `main.py` 改用相对导入（`from .src.xxx import ...`）。AstrBot 把插件目录整个当作包加载，原先的 `from src import ...` 因为找不到顶层 `src` 模块导致插件启动失败（`ModuleNotFoundError: No module named 'src'`）。

## v0.2.0 — 2026-04-30

### Added
- 新增 `tracked_repos` 配置：手动指定一批 GitHub 仓库 URL，cron 在 `push_time` 抓过去 24 小时新提交并由 LLM 总结后广播到所有 `target_sessions`。
- 新增 `/repo_track` 命令：基于当前 `tracked_repos` 立即拉取并回复（与 cron 走同一份纯函数，无副作用）。
- 新增 `tests/` pytest 套件，回归测试直接打真实 GitHub（trending HTML + commits REST API），不使用 fixture；`pre-push` hook 自动跑。

### Changed
- 模块化重构：`main.py` 拆为 `src/` 包（`models` / `config` / `github` / `llm` / `formatting` / `history` / `typing_heartbeat` / `pipeline`），`main.py` 仅作 AstrBot 适配层（命令注册、cron 注册、生命周期）。
- `pyproject.toml` 升级为标准 uv 项目，声明运行时与 `dev` / `scripts` 依赖组。
- 单仓库 24h 内提交数硬上限 50 条，超出在消息里标注"显示最近 50 条"。
- 所有追踪仓库 24h 都没新提交时，cron 段静默跳过，不发"暂无更新"。
