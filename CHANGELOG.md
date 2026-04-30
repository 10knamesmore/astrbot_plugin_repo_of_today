# Changelog

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
