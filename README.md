# Repo Of Today

A Telegram-friendly AstrBot plugin that fetches GitHub Trending repositories and supports daily scheduled push.

## Commands

- `/repo_today [language] [count]`
  - Fetch current daily trending repositories manually.
  - Examples:
    - `/repo_today`
    - `/repo_today python`
    - `/repo_today rust 3`

- `/repo_today_time HH:MM`
  - Set push time.
  - Example: `/repo_today_time 09:00`

- `/repo_today_langs <csv|all>`
  - Set subscribed languages array.
  - Example: `/repo_today_langs python,go,rust`
  - Use `all` to subscribe all languages.

- `/repo_today_count N`
  - Set push amount per language.
  - Range: `1-10`.

- `/repo_today_push on|off`
  - Enable or disable scheduled push.

- `/repo_today_config`
  - Show current configuration.

## Notes

- Configuration is stored per chat session (`UMO`), so each Telegram chat can use different settings.
- Scheduled push uses AstrBot cron manager.
