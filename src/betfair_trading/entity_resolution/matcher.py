"""Team name normalization and entity resolution between external data and Betfair."""

import re
from pathlib import Path

import structlog
import yaml

log = structlog.get_logger()


class TeamMatcher:
    def __init__(self, mappings_path: Path | None = None):
        # Normalized name -> canonical name
        self._mappings: dict[str, str] = {}
        if mappings_path and mappings_path.exists():
            self._load_mappings(mappings_path)

    def _load_mappings(self, path: Path) -> None:
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        for canonical, aliases in data.items():
            self._mappings[self._normalize(canonical)] = canonical
            if isinstance(aliases, list):
                for alias in aliases:
                    self._mappings[self._normalize(alias)] = canonical

        log.info("team_mappings_loaded", count=len(self._mappings))

    @staticmethod
    def _normalize(name: str) -> str:
        name = name.strip().lower()
        name = re.sub(r"[^a-z0-9\s]", "", name)
        name = re.sub(r"\s+", " ", name)
        # Common abbreviations
        name = name.replace(" fc", "").replace(" sc", "").replace(" cf", "")
        name = name.replace("united", "utd").replace("city", "")
        return name.strip()

    def resolve(self, name: str) -> tuple[str, float]:
        """Resolve a team name to canonical form.

        Returns (canonical_name, confidence_score).
        confidence: 1.0 = exact mapping, 0.5 = normalized match, 0.0 = unresolved.
        """
        # Direct mapping lookup
        normalized = self._normalize(name)
        if normalized in self._mappings:
            return self._mappings[normalized], 1.0

        # Check if already canonical
        if name in {v for v in self._mappings.values()}:
            return name, 1.0

        # Fallback: return as-is with low confidence
        log.warning("team_unresolved", raw_name=name, normalized=normalized)
        return name, 0.0

    def match_event(
        self,
        external_home: str,
        external_away: str,
        betfair_runners: list[str],
    ) -> tuple[dict[str, str] | None, float]:
        """Match external event teams to Betfair runner names.

        Returns (mapping dict {external_name: betfair_name}, confidence).
        """
        resolved_home, conf_h = self.resolve(external_home)
        resolved_away, conf_a = self.resolve(external_away)

        # Try to match resolved names against Betfair runners
        bf_normalized = {self._normalize(r): r for r in betfair_runners}

        mapping = {}
        matched = 0

        for resolved, external in [(resolved_home, external_home), (resolved_away, external_away)]:
            norm = self._normalize(resolved)
            if norm in bf_normalized:
                mapping[external] = bf_normalized[norm]
                matched += 1

        if matched == 2:
            return mapping, min(conf_h, conf_a)

        return None, 0.0
