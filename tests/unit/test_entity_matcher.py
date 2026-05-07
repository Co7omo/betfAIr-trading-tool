"""Tests for team name normalization and entity resolution."""

from pathlib import Path

from betfair_trading.entity_resolution.matcher import TeamMatcher


def test_normalize_strips_common_suffixes():
    assert TeamMatcher._normalize("Arsenal FC") == "arsenal"
    assert TeamMatcher._normalize("Real Madrid CF") == "real madrid"


def test_normalize_handles_abbreviations():
    norm = TeamMatcher._normalize("Manchester United")
    assert "utd" in norm


def test_resolve_with_mappings(tmp_path: Path):
    mappings = tmp_path / "teams.yaml"
    mappings.write_text('"Manchester United":\n  - "Man Utd"\n  - "Man United"\n')

    matcher = TeamMatcher(mappings)

    name, conf = matcher.resolve("Man Utd")
    assert name == "Manchester United"
    assert conf == 1.0


def test_resolve_unknown_team():
    matcher = TeamMatcher()
    name, conf = matcher.resolve("Unknown FC")
    assert conf == 0.0


def test_match_event_with_known_teams(tmp_path: Path):
    mappings = tmp_path / "teams.yaml"
    mappings.write_text('"Arsenal":\n  - "Arsenal FC"\n"Chelsea":\n  - "Chelsea FC"\n')

    matcher = TeamMatcher(mappings)

    mapping, conf = matcher.match_event(
        "Arsenal FC",
        "Chelsea FC",
        ["Arsenal", "Chelsea", "The Draw"],
    )

    assert mapping is not None
    assert mapping["Arsenal FC"] == "Arsenal"
    assert mapping["Chelsea FC"] == "Chelsea"
    assert conf == 1.0
