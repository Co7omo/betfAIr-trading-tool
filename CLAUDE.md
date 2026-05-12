# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**betfAIr Trading Tool MVP** — a pre-match AI-assisted trading system for Betfair Exchange football Match Odds (1X2). The system identifies temporary market inefficiencies before kick-off (T-120 to T-10 minutes), executes orders on positive net edge (after commission/slippage), and maintains complete auditability via an append-only event ledger.

## Development Commands

```bash
# Install dependencies
uv sync --all-extras

# Run all tests
uv run pytest -v

# Run a single test file
uv run pytest tests/unit/test_elo_engine.py -v

# Run only unit tests (default fast, no Docker)
uv run pytest -v -m "not integration"

# Run integration tests (requires Docker daemon running)
uv run pytest -v -m integration

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Train the baseline model from a results CSV
uv run python -m betfair_trading.training.train \
    --csv-path data/results.csv \
    --model-name logistic_v1 \
    --output-dir models/

# Run migrations (requires Postgres)
uv run alembic upgrade head

# Start full stack (Postgres + app + Grafana)
docker-compose up

# Run the app directly (requires Postgres + Betfair credentials in .env)
uv run python -m betfair_trading.main
```

## Tech Stack

- **Python 3.12+** with asyncio for I/O concurrency
- **uv** for package management
- **PostgreSQL 16** via asyncpg (writes) + SQLAlchemy Core (reads)
- **Alembic** for schema migrations (raw SQL)
- **Pydantic** for data contracts, **pydantic-settings** for config
- **structlog** (JSON) for structured logging with correlation IDs
- **betfairlightweight** wrapped in async adapter (`asyncio.to_thread()`)
- **Docker + docker-compose** for local dev
- **ruff** for linting + formatting
- **pytest + pytest-asyncio** for testing

## Repository Structure

- `src/betfair_trading/` — Application source code
  - `models/` — Pydantic contracts (market, external, features, common)
  - `db/` — asyncpg pool, append-only INSERT writers, SQLAlchemy Core table defs
  - `betfair_client/` — Async wrapper around betfairlightweight
  - `services/` — Market Collector, External Ingestor, Feature Builder, Scheduler
  - `elo/` — Elo engine + form calculator with strict as-of semantics
  - `entity_resolution/` — Team name normalization + matching
  - `observability/` — structlog config + health check endpoint
- `alembic/versions/` — SQL schema migrations
- `config/` — Runtime trading params (YAML) + team mappings
- `tests/` — Unit and integration tests
- `docs/` — Architecture specs, product requirements, ADRs

## Key Architecture Concepts

**Currently implemented (Phases 1-2):** Market Data Collector, External Data Ingestor, Feature Builder (A0+A1+A2), Scheduler, DB audit layer, Decision Engine + risk gates, Model Inference baseline (LogReg + Platt calibration on Elo+form features).

**Not yet implemented:** Execution Engine, P&L Engine, Kafka messaging.

**Data flow:** Market polling (10s) + external data (Elo/form) -> Feature Builder -> Model Inference -> Decision Engine (edge check + risk gates) -> Execution Engine -> Betfair API. All steps append to the audit ledger.

**Decision policy requires ALL gates to pass:** net edge >= threshold, liquidity/spread within bounds, time window T-120 to T-10, position <= 2% bankroll, fractional Kelly 0.25, daily drawdown < 5%, max 1 position per event.

**Feature stages (ablation):** A0 = market-only, A1 = market + Elo, A2 = market + Elo + form. Strict as-of timestamps to prevent data leakage.

**Audit-first data model:** All tables are append-only (INSERT only, no UPDATE/DELETE). Key correlation identifiers: `decision_id`, `customerOrderRef`, `market_id`, `model_version`, `feature_set_version`.

## Critical Correctness Constraints

- **Anti-leakage:** `EloEngine.get_ratings_asof(ts)` and `FormCalculator.compute_form(team, ts, n)` must never use matches completed after `ts`. This is tested in `test_elo_engine.py` and `test_form_features.py`.
- **Append-only:** DB writer functions only INSERT, never UPDATE. Tables have no UPDATE/DELETE triggers.
- **Feature hash reproducibility:** `FeatureVector.feature_hash` is SHA256 of canonical JSON — same inputs must always produce the same hash.
- **Idempotency:** Orders use `customerOrderRef` for deduplication and reconciliation.

## Key Decisions (ADRs)

- **ADR-0001:** Pre-match only scope (no in-play)
- **ADR-0002:** Supervised signal using market + Elo + form features
- **ADR-0003:** Settlement-based realized P&L (no active hedging)
- **No Kafka in Phase 1:** Single async process with direct function calls. Pydantic contracts (e.g., `MarketSnapshotBundle`) are designed so Kafka migration is transport-only.
- **No ORM:** asyncpg raw SQL for writes, SQLAlchemy Core for reads. Append-only tables don't benefit from ORM change tracking.
