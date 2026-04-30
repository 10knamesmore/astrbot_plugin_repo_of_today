from __future__ import annotations

from src.config import (
    build_runtime_config,
    normalize_language,
    normalize_time,
    parse_time_to_hm,
    target_sessions,
    track_show_count,
    tracked_repos,
)


class TestTrackedRepos:
    def test_parses_https(self):
        cfg = {"tracked_repos": ["https://github.com/AstrBotDevs/AstrBot"]}
        assert tracked_repos(cfg) == [("AstrBotDevs", "AstrBot")]

    def test_trailing_slash(self):
        cfg = {"tracked_repos": ["https://github.com/owner/repo/"]}
        assert tracked_repos(cfg) == [("owner", "repo")]

    def test_dot_git(self):
        cfg = {"tracked_repos": ["https://github.com/owner/repo.git"]}
        assert tracked_repos(cfg) == [("owner", "repo")]

    def test_tree_branch(self):
        cfg = {"tracked_repos": ["https://github.com/owner/repo/tree/main"]}
        assert tracked_repos(cfg) == [("owner", "repo")]

    def test_blob_path(self):
        cfg = {"tracked_repos": ["https://github.com/owner/repo/blob/main/README.md"]}
        assert tracked_repos(cfg) == [("owner", "repo")]

    def test_ssh(self):
        cfg = {"tracked_repos": ["git@github.com:owner/repo.git"]}
        assert tracked_repos(cfg) == [("owner", "repo")]

    def test_whitespace_stripped(self):
        cfg = {"tracked_repos": ["  https://github.com/Soulter/AstrBot  "]}
        assert tracked_repos(cfg) == [("Soulter", "AstrBot")]

    def test_invalid_dropped(self):
        cfg = {
            "tracked_repos": [
                "https://example.com/owner/repo",
                "https://github.com/owner",
                "https://github.com/owner/",
                "",
                None,
                123,
            ]
        }
        assert tracked_repos(cfg) == []

    def test_mixed(self):
        cfg = {
            "tracked_repos": [
                "https://github.com/a/b",
                "garbage",
                "https://github.com/c/d.git",
            ]
        }
        assert tracked_repos(cfg) == [("a", "b"), ("c", "d")]

    def test_empty_or_missing(self):
        assert tracked_repos({}) == []
        assert tracked_repos({"tracked_repos": []}) == []
        assert tracked_repos({"tracked_repos": "not a list"}) == []


class TestTargetSessions:
    def test_basic(self):
        cfg = {"target_sessions": ["telegram:GroupMessage:1", "qq:PrivateMessage:2"]}
        assert target_sessions(cfg) == [
            "telegram:GroupMessage:1",
            "qq:PrivateMessage:2",
        ]

    def test_strips_and_filters(self):
        cfg = {"target_sessions": ["  a  ", "", None, "b"]}
        assert target_sessions(cfg) == ["a", "b"]

    def test_invalid_type(self):
        assert target_sessions({"target_sessions": "x"}) == []
        assert target_sessions({}) == []


class TestNormalizeTime:
    def test_valid(self):
        assert normalize_time("09:00") == "09:00"
        assert normalize_time("9:05") == "09:05"
        assert normalize_time("23:59") == "23:59"
        assert normalize_time("  00:00  ") == "00:00"

    def test_invalid(self):
        assert normalize_time("24:00") is None
        assert normalize_time("9:60") is None
        assert normalize_time("garbage") is None
        assert normalize_time("") is None


class TestParseTimeToHm:
    def test_valid(self):
        assert parse_time_to_hm("09:30") == (9, 30)
        assert parse_time_to_hm("23:00") == (23, 0)

    def test_invalid_raises(self):
        import pytest

        with pytest.raises(ValueError):
            parse_time_to_hm("not a time")


class TestNormalizeLanguage:
    def test_basic(self):
        assert normalize_language("Python") == "python"
        assert normalize_language("C++") == "c++"
        assert normalize_language("C#") == "c#"
        assert normalize_language("Objective-C") == "objective-c"
        assert normalize_language("  Rust  ") == "rust"

    def test_strips_unsafe_chars(self):
        assert normalize_language("foo!bar") == "foobar"


class TestBuildRuntimeConfig:
    def test_defaults_when_empty(self):
        cfg = {}
        rc = build_runtime_config(cfg, "telegram:GroupMessage:1")
        assert rc.session_umo == "telegram:GroupMessage:1"
        assert rc.push_time == "09:00"
        assert rc.push_count == 5
        assert rc.languages == []
        assert rc.tracked_repos == []

    def test_clamps_count(self):
        rc = build_runtime_config({"push_count": 999}, "umo")
        assert rc.push_count == 10  # MAX_RESULTS_LIMIT
        rc = build_runtime_config({"push_count": -3}, "umo")
        assert rc.push_count == 1

    def test_invalid_count_falls_back(self):
        rc = build_runtime_config({"push_count": "abc"}, "umo")
        assert rc.push_count == 5

    def test_languages_normalized(self):
        rc = build_runtime_config({"languages": ["Python", "Rust", " "]}, "umo")
        assert rc.languages == ["python", "rust"]

    def test_tracked_repos_propagated(self):
        rc = build_runtime_config(
            {"tracked_repos": ["https://github.com/a/b"]},
            "umo",
        )
        assert rc.tracked_repos == [("a", "b")]

    def test_track_show_count_propagated(self):
        rc = build_runtime_config({"track_show_count": 7}, "umo")
        assert rc.track_show_count == 7


class TestTrackShowCount:
    def test_default(self):
        assert track_show_count({}) == 5

    def test_clamps_high(self):
        assert track_show_count({"track_show_count": 999}) == 50

    def test_clamps_low(self):
        assert track_show_count({"track_show_count": 0}) == 1
        assert track_show_count({"track_show_count": -10}) == 1

    def test_invalid_falls_back(self):
        assert track_show_count({"track_show_count": "abc"}) == 5
        assert track_show_count({"track_show_count": None}) == 5

    def test_int_string_accepted(self):
        assert track_show_count({"track_show_count": "7"}) == 7
