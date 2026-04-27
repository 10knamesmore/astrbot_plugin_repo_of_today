# Repo Of Today

一个 AstrBot 插件：抓取 GitHub Trending 仓库，并按每日定时计划推送到订阅会话。

## 指令

- `/repo_today`
  - 基于当前配置手动拉取一次每日热门仓库。
  - 无参数 — 所有行为通过下文的 WebUI 配置控制。

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

### 如何获取 UMO

UMO 是会话的唯一标识。最简单的获取方式：在目标会话里随便发一条消息，然后到 AstrBot 日志中查最近一条的 `unified_msg_origin`，复制填入 `target_sessions` 即可。

### 更新语言下拉选项

`languages.options` 是离线从 [github-linguist/linguist](https://github.com/github-linguist/linguist) 生成的。如需刷新：

```bash
pip install pyyaml requests
python scripts/update_languages.py
```

脚本会就地重写 `_conf_schema.json`，提交 diff 即可。

## 说明

- `target_sessions` 中的所有会话共享同一份配置，推送内容一致。
- 定时推送由 AstrBot 的 cron manager 驱动；切换 `enabled` 后重载插件即会重新注册任务。
- 仓库描述会经由当前会话的 LLM Provider 重写，并尊重当前启用人格的 `system_prompt`。
