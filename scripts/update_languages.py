"""离线维护脚本：从 GitHub Linguist 拉取语言列表并更新 _conf_schema.json。

用法：
    python scripts/update_languages.py

依赖（仅维护时需要，不写入插件 requirements.txt）：
    pip install pyyaml requests
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests
import yaml

LINGUIST_URL = (
    "https://raw.githubusercontent.com/github-linguist/linguist/main/"
    "lib/linguist/languages.yml"
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "_conf_schema.json"


def normalize_language(name: str) -> str:
    """与 main.py:_normalize_language 保持一致的标准化。"""
    return re.sub(r"[^a-z0-9_+#-]", "", name.strip().lower())


def fetch_languages() -> list[str]:
    """拉取 linguist languages.yml，返回标准化后的编程语言名集合。"""
    resp = requests.get(LINGUIST_URL, timeout=30)
    resp.raise_for_status()
    data = yaml.safe_load(resp.text)

    seen: set[str] = set()
    result: list[str] = []
    for raw_name, meta in data.items():
        if not isinstance(meta, dict):
            continue
        if meta.get("type") != "programming":
            continue
        normalized = normalize_language(str(raw_name))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    result.sort()
    return result


def update_schema(languages: list[str]) -> None:
    """In-place 更新 _conf_schema.json 的 languages.options 字段。"""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if "languages" not in schema:
        raise SystemExit(f"languages field not found in {SCHEMA_PATH}")
    schema["languages"]["options"] = languages
    SCHEMA_PATH.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    languages = fetch_languages()
    if not languages:
        print("no languages parsed; aborting", file=sys.stderr)
        return 1
    update_schema(languages)
    print(f"updated {SCHEMA_PATH} with {len(languages)} languages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
