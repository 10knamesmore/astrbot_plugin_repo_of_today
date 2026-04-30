# Repo Of Today

一个 AstrBot 插件：抓取 GitHub Trending 仓库并按每日定时计划推送到订阅会话；同时支持手动指定一批关心的仓库，按同一计划抓取它们过去 24 小时的新提交并由 LLM 总结。

## 指令

- `/repo_today`
  - 基于当前配置手动拉取一次每日热门仓库。
- `/repo_track`
  - 立即拉取已配置追踪仓库（`tracked_repos`）过去 24 小时的提交并 LLM 总结。无副作用。

## 配置（WebUI）

所有设置定义在 `_conf_schema.json` 中，可在 AstrBot WebUI 的插件页直接编辑：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `enabled` | bool | 是否启用每日定时推送，默认 `false`。 |
| `append_to_message_chain` | bool | 推送后是否将消息以 `assistant` 角色追加到对应会话当前对话的 history（供 LLM 上下文使用），默认 `false`。 |
| `push_time` | string | 每日推送时间，格式 `HH:MM`（24 小时制），默认 `09:00`。 |
| `push_count` | int | 每种语言推送的仓库数量（建议 1–10），默认 `5`。 |
| `languages` | list | 关注语言的多选下拉；为空表示推送全语言榜。 |
| `target_sessions` | list | 推送目标会话的 UMO（`unified_msg_origin`）列表，例如 `telegram:GroupMessage:123456`。 |
| `tracked_repos` | list | 手动追踪的仓库 URL 列表，例如 `https://github.com/AstrBotDevs/AstrBot`；启用时每日 `push_time` 抓取过去 24 小时新提交并 LLM 总结。留空则禁用追踪推送。 |

`tracked_repos` 接受常见 URL 形态：仓库主页 / 末尾斜杠 / `.git` 后缀 / `/tree/<branch>` 子页面 / SSH 形式 `git@github.com:owner/repo.git`，都会被正则解析为 `owner/repo`。所有追踪仓库 24 小时内都没有新提交时，cron 段会静默跳过，不发"暂无更新"。

### 如何获取 UMO

UMO 是会话的唯一标识。最简单的获取方式：在目标会话里随便发一条消息，然后到 AstrBot 日志中查最近一条的 `unified_msg_origin`，复制填入 `target_sessions` 即可。

## 说明

- `target_sessions` 中的所有会话共享同一份配置，推送内容一致。
- 定时推送由 AstrBot 的 cron manager 驱动；切换 `enabled` 后重载插件即会重新注册任务。
- 仓库描述与提交总结都会经由当前会话的 LLM Provider 生成，并尊重当前启用人格的 `system_prompt`。
- 每仓库 24 小时内的提交数硬上限 50 条（防 prompt 爆炸），超出会在消息里标注"显示最近 50 条"。
- GitHub commits 抓取走匿名 REST API（60 次/小时），对常见用量足够，无需 token。

## 本地开发

本仓库是一个标准的 uv 项目，依赖在 `pyproject.toml` 里声明，AstrBot 运行时所需的最小依赖在 `requirements.txt` 中（AstrBot 加载插件时会安装）。

### 回归测试

`tests/` 是 pytest 套件。设计上不使用 fixture 兜底：

- `test_trending_live.py`、`test_commits_live.py` 直接打真实 GitHub（Trending HTML + REST commits API），断言字段齐全、错误兜底、截断生效。GitHub 改 HTML 结构或 JSON 字段时立刻报警。
- `test_config.py`、`test_formatting.py` 是纯函数（URL 解析、消息渲染），无网络依赖。

运行：

```bash
uv run --group dev pytest -q
```

总耗时约 9s（瓶颈是真实 GitHub 调用）。匿名速率 60/h，正常开发节奏不会触顶。

### 静态检查

```bash
uv run --group dev ruff check main.py src/
uv run --group dev ruff format --check main.py src/
```

### 防回归 hook

`.pre-commit-config.yaml` 已配置：

- `pre-commit` 阶段：`ruff-check --fix`、`ruff-format`、`pyupgrade --py310-plus`。
- `pre-push` 阶段：`uv run --group dev pytest -q`，回归测试（含真实 GitHub 调用）通过才允许推送。

启用方式（一次即可）：

```bash
uv tool run pre-commit install --install-hooks
```

之后每次 `git commit` / `git push` 自动触发对应 stage 的 hook。也可以手动跑：

```bash
uv tool run pre-commit run --all-files                       # 跑所有 commit-stage hook
uv tool run pre-commit run --hook-stage pre-push --all-files # 跑 pre-push（含 pytest）
```

### 更新语言下拉选项

`languages.options` 是离线从 [github-linguist/linguist](https://github.com/github-linguist/linguist) 生成的。如需刷新：

```bash
uv run --group scripts python scripts/update_languages.py
```

脚本会就地重写 `_conf_schema.json`，提交 diff 即可。

## 模块结构

```
main.py                  # AstrBot 适配层：Star 子类、命令、cron 注册、生命周期
src/
├── __init__.py          # 共享常量
├── models.py            # dataclass：RepoItem / PushConfig / RepoCommit / RepoUpdate
├── config.py            # 配置读取与归一化（含 tracked_repos URL 解析）
├── github/
│   ├── trending.py      # GitHub Trending HTML 抓取与解析
│   └── commits.py       # GitHub REST API 抓取追踪仓库提交
├── llm.py               # 人格解析 + LLM 增强（trending 描述、开场语、commits 总结）
├── formatting.py        # Markdown 文本拼装
├── history.py           # 写入会话对话历史
├── typing_heartbeat.py  # 命令处理期间的 typing 心跳
└── pipeline.py          # 业务流编排：run_trending / run_track / run_*_once
scripts/
└── update_languages.py  # 重新生成语言下拉列表
tests/                   # pytest 套件（含真实 GitHub 调用）
```

`main.py` 仅保留对 AstrBot API 的直接调用（命令注册、cron 注册、`event.plain_result`、`ctx.send_message`）；所有真实业务逻辑都在 `src/`。
