# Integration Test End-to-End — Design

**Data:** 2026-05-07
**Scope:** Phase 1 data pipeline (MarketCollector + ExternalDataIngestor + FeatureBuilder + DB audit layer)
**Out of scope:** Phase 2+ (Model Inference, Decision Engine, Execution, P&L)

## 1. Obiettivo

Coprire con test end-to-end il flusso dati implementato in Phase 1 prima di iniziare Phase 2, in modo che ogni feature aggiuntiva nasca su una base regression-safe. I test devono esercitare i componenti reali (Pydantic models, asyncpg pool, INSERT writers, Scheduler/Collector, FeatureBuilder, EloEngine, FormCalculator, TeamMatcher) sostituendo solo i confini esterni: il client Betfair e il database.

Sostituzioni:

- **Betfair API** → `FakeAsyncBetfairClient` in-process con payload deterministici
- **Postgres** → istanza effimera via `testcontainers-python` con migrazioni Alembic applicate

Tutto il resto è codice di produzione.

## 2. Test Infrastructure

### 2.1 Postgres effimero

- Dependency dev nuova: `testcontainers[postgres]>=4.8.0`
- Container scope `session` (un solo container per intera run pytest)
- Image `postgres:16` per allinearsi a `docker-compose.yml`
- Fixture `pg_container` espone `database_url` su porta random
- Fixture `migrated_db` (session-scoped, dipende da `pg_container`) applica le migrazioni Alembic via `alembic.config.Config` + `alembic.command.upgrade(cfg, "head")` settando `sqlalchemy.url` programmaticamente

### 2.2 Pool asyncpg

- Fixture `pg_pool` (session-scoped, dipende da `migrated_db`): apre un `asyncpg.Pool` riusato tra test, come in produzione. Questo è importante: garantisce che eventuali bug di concorrenza/connection-handling emergano nei test.

### 2.3 Isolamento per-test

- Fixture `clean_db` (function-scoped, `autouse=True` solo nei moduli `tests/integration/`): esegue
  `TRUNCATE markets, runners, market_snapshots, external_feature_snapshots, feature_vectors, config_snapshots RESTART IDENTITY CASCADE`
  prima di ogni test. Le tabelle sono append-only, quindi TRUNCATE su tabelle vuote è ~ms.

### 2.4 Markers e separazione unit/integration

- Aggiungere in `pyproject.toml`:
  ```toml
  [tool.pytest.ini_options]
  markers = ["integration: requires Postgres testcontainer"]
  ```
- Tutti i moduli sotto `tests/integration/` usano `pytestmark = pytest.mark.integration` a livello modulo
- Esecuzione:
  - `uv run pytest -v -m "not integration"` → solo unit (default veloce, no Docker)
  - `uv run pytest -v -m integration` → solo integration
  - `uv run pytest -v` → tutto

## 3. Struttura file

```
tests/
├── conftest.py                          # invariato (fixture unit esistenti)
├── unit/                                 # invariato
└── integration/
    ├── __init__.py
    ├── conftest.py                       # pg_container, migrated_db, pg_pool, clean_db
    ├── fakes/
    │   ├── __init__.py
    │   ├── fake_betfair_client.py        # FakeAsyncBetfairClient
    │   └── fixtures.py                   # builder per market/runner/book payload
    ├── test_pipeline_collector.py
    ├── test_pipeline_feature_builder.py
    ├── test_pipeline_external_data.py
    └── test_pipeline_edge_cases.py
```

## 4. FakeAsyncBetfairClient

Espone la stessa surface del `AsyncBetfairClient` reale usata da `MarketCollector`/`Scheduler`: `list_market_catalogue`, `list_market_book`, `keep_alive`. La surface esatta va verificata leggendo `src/betfair_trading/betfair_client/client.py` e `services/market_collector.py` durante l'implementazione: lo spec assume questi tre metodi e qualunque deviazione diventa un follow-up nel plan.

```python
class FakeAsyncBetfairClient:
    def __init__(self):
        self._catalogue: list[dict] = []
        self._books: dict[str, list[dict]] = {}     # market_id → sequenza di book
        self._book_call_count: dict[str, int] = {}

    # Builder API per i test
    def add_market(self, market_id, event_id, start_time, runners) -> None: ...
    def queue_book(self, market_id, book_payload) -> None: ...

    # Surface client
    async def list_market_catalogue(self, filter, **kw) -> list[dict]: ...
    async def list_market_book(self, market_ids, **kw) -> list[dict]: ...
    async def keep_alive(self) -> None: ...
```

I payload restituiti sono dict coerenti con quello che il client reale (basato su `betfairlightweight`) consegna oggi al `MarketCollector`. La forma esatta va estratta dai mapping già presenti in `services/market_collector.py` per evitare drift.

`fakes/fixtures.py` espone helper per evitare boilerplate nei test:

- `make_market(market_id, event_id, home, away, start_time, competition="EPL")`
- `make_book(market_id, runners=[(runner_id, back, lay, size_back, size_lay)], status="OPEN", inplay=False, total_matched=0.0)`

## 5. Scenari di test

I metodi `MarketCollector.run_discovery()` e `MarketCollector.run_poll_cycle(on_snapshot=...)` sono già pubblici; i test li chiamano direttamente senza avviare lo `Scheduler.run()` infinito.

### 5.1 `test_pipeline_collector.py` — discovery + polling

1. **`test_discovery_persists_markets_and_runners`** — Fake espone 2 market in finestra; `collector.run_discovery()` → tabella `markets` ha 2 righe, `runners` ha 6 (3 runner × 2 market). Conferma valori chiave: `event_id`, `start_time`, `runner_name`.
2. **`test_polling_persists_snapshots`** — 1 market discoverato, fake `queue_book` 3 book diversi; 3 chiamate a `run_poll_cycle` → `market_snapshots` ha 9 righe (3 runner × 3 cycle), valori `best_back_price`/`best_lay_price` coerenti con i book in coda.
3. **`test_minutes_to_start_computed`** — Verifica `minutes_to_start` sul snapshot vs `start_time - snapshot_ts` con tolleranza ±1s.

### 5.2 `test_pipeline_feature_builder.py` — Feature Builder A0

1. **`test_a0_feature_vector_written`** — Snapshot persistito triggera `feature_builder.on_market_snapshot`; tabella `feature_vectors` ha 1 riga con `feature_set_version='A0'`, `snapshot_id` linkato, `ext_snapshot_id` NULL (A0 è market-only).
2. **`test_feature_hash_deterministic`** — Stesso input snapshot due volte → stesso `feature_hash`; cambiare anche un solo prezzo → hash diverso. Garantisce SHA256 di canonical JSON.
3. **`test_feature_vector_links_correct_snapshot`** — 2 snapshot per stesso market a tempi diversi → 2 feature_vector, ognuno punta al proprio `snapshot_id`.

### 5.3 `test_pipeline_external_data.py` — Elo + form as-of

1. **`test_load_historical_results_populates_elo_form`** — Ingestor carica un CSV fixture (≤20 match); `EloEngine.get_ratings_asof(t)` restituisce rating consistenti con i match precedenti `t`; `FormCalculator.compute_form(team, t, n=5)` ritorna il valore atteso calcolato a mano nel test.
2. **`test_asof_excludes_future_matches`** — Risultati con date `[d-30d, d-10d, d+5d, d+10d]`; `get_ratings_asof(d)` non include i due match futuri (anti-leakage). Anche `compute_form(team, d, 10)` esclude i futuri.
3. **`test_external_snapshot_persisted`** — Ingestor scrive su `external_feature_snapshots` con `elo_home`, `elo_away`, `form_home_5`, `form_away_5`, `match_confidence='HIGH'` quando il team match è esatto.

### 5.4 `test_pipeline_edge_cases.py`

1. **`test_market_outside_window_skipped`** — Market con `start_time = now + 200min` e `now - 5min` (T+0): `run_poll_cycle` non scrive snapshot per quei market. `market_snapshots` count = 0.
2. **`test_suspended_market_snapshot_recorded_with_status`** — Book con `status=SUSPENDED`: snapshot scritto comunque (audit-first), ma `market_status='SUSPENDED'`. Niente scarto silenzioso.
3. **`test_entity_match_miss_does_not_break_pipeline`** — Fixture con team non presenti in `team_mappings.yaml`: `external_feature_snapshots` scritto con `match_confidence='NONE'` (o `'LOW'`, da verificare con la logica corrente di `TeamMatcher`) e `quality_flags` che riflettono il fallimento. Il flusso continua: `market_snapshots` e `feature_vectors` vengono comunque scritti.

## 6. Dipendenze nuove

Modifica `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.25.0",
    "ruff>=0.8.0",
    "testcontainers[postgres]>=4.8.0",
]
```

Niente altro.

## 7. Performance attese

- Avvio container Postgres: ~3–5s (una volta per session)
- Migrazione Alembic: ~1s (una volta per session)
- Singolo test integration: <2s dopo warm-up
- Suite integration completa stimata: <60s

## 8. Vincoli e principi

- **Append-only verificato**: i test non devono usare UPDATE/DELETE; il TRUNCATE è solo nel teardown della fixture, non dentro il codice testato.
- **Production-like**: il pool asyncpg multi-connessione è il path reale; non usare single-connection mode "per facilità".
- **Anti-leakage in evidenza**: `test_asof_excludes_future_matches` è uno dei test più importanti — è l'invariante di correttezza più costosa da scoprire in produzione.
- **Audit-first**: scenari come "suspended" e "entity match miss" verificano che la pipeline scriva sempre traccia, anche in degraded path.

## 9. Non in questo spec

- Test contro Postgres locale del docker-compose dev (esplicitamente rifiutato a favore di testcontainers)
- Mocking via HTTP (respx/aiohttp) — non utile dato che `betfairlightweight` è il vero confine, non l'HTTP raw
- Recorded fixtures stile vcr.py
- Test per Phase 2+ (non implementata)
- Configurazione CI (GitHub Actions ecc.) — può essere un follow-up separato
