"""External Data Ingestor: loads historical results, computes Elo + form with as-of semantics."""

import csv
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import structlog

from betfair_trading.elo.engine import EloEngine, MatchResult
from betfair_trading.elo.form import FormCalculator
from betfair_trading.entity_resolution.matcher import TeamMatcher
from betfair_trading.models.external import ExternalFeatureBundle

log = structlog.get_logger()


class ExternalDataIngestor:
    def __init__(
        self,
        elo_engine: EloEngine,
        form_calculator: FormCalculator,
        team_matcher: TeamMatcher,
        db_pool: asyncpg.Pool,
    ):
        self._elo = elo_engine
        self._form = form_calculator
        self._matcher = team_matcher
        self._pool = db_pool
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    async def load_historical_results(self, csv_path: str | Path) -> int:
        """Load historical match results from CSV and build Elo/form timelines.

        Expected CSV columns: Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR
        (football-data.co.uk format)

        FTR: H = Home Win, D = Draw, A = Away Win
        FTHG/FTAG: Full Time Home/Away Goals
        """
        path = Path(csv_path)
        if not path.exists():
            log.warning("results_file_not_found", path=str(path))
            return 0

        matches = []
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    # Parse date - try common formats
                    date_str = row.get("Date", "")
                    dt = self._parse_date(date_str)
                    if dt is None:
                        continue

                    home = row.get("HomeTeam", "").strip()
                    away = row.get("AwayTeam", "").strip()
                    ftr = row.get("FTR", "").strip()
                    fthg = int(row.get("FTHG", 0))
                    ftag = int(row.get("FTAG", 0))

                    if not (home and away and ftr):
                        continue

                    result_map = {
                        "H": MatchResult.HOME_WIN,
                        "D": MatchResult.DRAW,
                        "A": MatchResult.AWAY_WIN,
                    }
                    result = result_map.get(ftr)
                    if result is None:
                        continue

                    matches.append((dt, home, away, result, fthg, ftag))
                except (ValueError, KeyError):
                    continue

        # Sort chronologically and apply
        matches.sort(key=lambda m: m[0])

        for dt, home, away, result, fthg, ftag in matches:
            self._elo.apply_result(home, away, result, dt)
            self._form.add_match(home, away, result, fthg, ftag, dt)

        self._loaded = True
        log.info(
            "historical_results_loaded",
            matches=len(matches),
            teams=len(self._elo._ratings),
            elo_history=self._elo.history_size,
        )
        return len(matches)

    def get_features_asof(
        self,
        home_team: str,
        away_team: str,
        asof_ts: datetime,
        market_id: str | None = None,
    ) -> ExternalFeatureBundle | None:
        """Get Elo + form features strictly as-of the given timestamp."""
        # Resolve team names
        resolved_home, conf_h = self._matcher.resolve(home_team)
        resolved_away, conf_a = self._matcher.resolve(away_team)

        confidence = min(conf_h, conf_a)
        quality_flags = {
            "home_confidence": conf_h,
            "away_confidence": conf_a,
            "resolved_home": resolved_home,
            "resolved_away": resolved_away,
            "history_loaded": self._loaded,
        }

        # Get Elo ratings as-of
        elo_home, elo_away = self._elo.get_ratings_asof(resolved_home, resolved_away, asof_ts)
        elo_delta = elo_home - elo_away

        # Get form features as-of
        form_home_5 = self._form.compute_form(resolved_home, asof_ts, n=5)
        form_away_5 = self._form.compute_form(resolved_away, asof_ts, n=5)
        form_home_10 = self._form.compute_form(resolved_home, asof_ts, n=10)
        form_away_10 = self._form.compute_form(resolved_away, asof_ts, n=10)

        event_key = f"{resolved_home}_vs_{resolved_away}"
        match_confidence = "HIGH" if confidence >= 0.8 else "LOW" if confidence < 0.5 else "MEDIUM"

        return ExternalFeatureBundle(
            event_key=event_key,
            market_id=market_id,
            asof_ts=asof_ts,
            home_team=resolved_home,
            away_team=resolved_away,
            elo_home=Decimal(str(round(elo_home, 2))),
            elo_away=Decimal(str(round(elo_away, 2))),
            elo_delta=Decimal(str(round(elo_delta, 2))),
            form_home_5=form_home_5,
            form_away_5=form_away_5,
            form_home_10=form_home_10,
            form_away_10=form_away_10,
            match_confidence=match_confidence,
            quality_flags=quality_flags,
        )

    @staticmethod
    def _parse_date(date_str: str) -> datetime | None:
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.replace(tzinfo=UTC)
            except ValueError:
                continue
        return None
