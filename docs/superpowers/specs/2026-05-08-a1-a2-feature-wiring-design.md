# A1/A2 Feature Wiring — Design

**Data:** 2026-05-08
**Scope:** Cablare `ExternalDataIngestor` dentro `FeatureBuilder` per produrre feature_vector A1 (market+Elo) e A2 (market+Elo+form) accanto al baseline A0, e persistere gli `external_feature_snapshots` come parte del flusso live.
**Out of scope:** Model Inference, Decision Engine, Execution. Cambia solo la pipeline feature.

## 1. Obiettivo

Phase 1 ha messo in piedi A0 (market-only) e ha lasciato `ExternalDataIngestor` non collegato al `FeatureBuilder`. Questa iterazione completa la pipeline feature con A1 e A2 in modalità ablation completa: per ogni snapshot scriviamo tutti e tre i livelli, così che a posteriori sia possibile confrontare A0/A1/A2 sulla stessa decisione.

Vincoli mantenuti:
- **Anti-leakage**: as-of strict (`snapshot_ts` del primo snapshot del market).
- **Audit-first**: `external_feature_snapshots` viene scritto anche con team confidence LOW; `quality_flags` racconta la degradazione.
- **Append-only**: nessun UPDATE; cache in-memory invalida solo per la lifecycle del process.

## 2. Architettura

### 2.1 Flusso `FeatureBuilder.on_market_snapshot`

Pseudocodice:

```
async def on_market_snapshot(bundle, snapshot_ids):
    ext_snapshot_id, ext_bundle = await self._get_or_create_external(bundle)

    feature_vector_ids = []
    async with self._pool.acquire() as conn:
        for i, runner in enumerate(bundle.runners):
            snap_id = snapshot_ids[i] if i < len(snapshot_ids) else None
            a0 = self._build_a0(bundle, runner)
            feature_vector_ids.append(
                await insert_feature_vector(conn, FeatureVector(
                    market_id=bundle.market_id, event_id=bundle.event_id,
                    runner_id=runner.runner_id,
                    feature_set_version=FeatureSetVersion.A0,
                    snapshot_id=snap_id, ext_snapshot_id=None,
                    features=a0, generated_at=now_utc(),
                ))
            )
            if ext_snapshot_id is not None:
                a1 = self._build_a1(a0, ext_bundle)
                a2 = self._build_a2(a1, ext_bundle)
                feature_vector_ids.append(
                    await insert_feature_vector(conn, FeatureVector(... A1 ..., features=a1))
                )
                feature_vector_ids.append(
                    await insert_feature_vector(conn, FeatureVector(... A2 ..., features=a2))
                )
    return feature_vector_ids
```

### 2.2 Cache nel FeatureBuilder

```python
class FeatureBuilder:
    def __init__(self, db_pool, external_ingestor=None):
        self._pool = db_pool
        self._ingestor = external_ingestor
        self._ext_id_cache: dict[str, UUID] = {}              # market_id → ext_snapshot_id
        self._ext_bundle_cache: dict[str, ExternalFeatureBundle] = {}  # market_id → bundle
        self._runner_meta_cache: dict[str, list[Runner]] = {}  # market_id → catalogue runners
```

Le tre cache sono parallele e popolate insieme la prima volta che si vede un `market_id`. Si svuotano alla fine del processo (no eviction esplicita: il cardinal di mercati attivi è O(100), non un problema di memoria).

### 2.3 Estrazione home/away

I `RunnerSnapshot` nel `MarketSnapshotBundle` non hanno `runner_name` né `sort_priority`. Questi vivono nella tabella `runners` (popolata da `MarketCollector` durante `run_discovery`). Il FeatureBuilder li carica una volta per market via SQL:

```python
async def _load_runner_metadata(self, conn, market_id) -> list[Runner]:
    if market_id in self._runner_meta_cache:
        return self._runner_meta_cache[market_id]
    rows = await conn.fetch(
        "SELECT runner_id, runner_name, sort_priority FROM runners "
        "WHERE market_id = $1 ORDER BY sort_priority NULLS LAST, runner_id",
        market_id,
    )
    runners = [Runner(runner_id=r["runner_id"], runner_name=r["runner_name"],
                      sort_priority=r["sort_priority"]) for r in rows]
    self._runner_meta_cache[market_id] = runners
    return runners
```

Estrazione team:

```python
@staticmethod
def _extract_teams(runners: list[Runner]) -> tuple[str, str]:
    """Betfair Match Odds: sort_priority 1=home, 2=draw, 3=away."""
    sorted_runners = sorted(runners, key=lambda r: (r.sort_priority is None, r.sort_priority))
    return sorted_runners[0].runner_name, sorted_runners[-1].runner_name
```

Il `(r.sort_priority is None, r.sort_priority)` mette i `None` in coda, evita `TypeError` su `sort_priority is None`. In produzione i Match Odds hanno sempre 3 runner ben ordinati, ma il fallback rende il codice difensivo.

### 2.4 `_get_or_create_external`

```python
async def _get_or_create_external(self, bundle):
    if self._ingestor is None:
        return None, None
    if bundle.market_id in self._ext_id_cache:
        return self._ext_id_cache[bundle.market_id], self._ext_bundle_cache[bundle.market_id]

    async with self._pool.acquire() as conn:
        runners = await self._load_runner_metadata(conn, bundle.market_id)
        home, away = self._extract_teams(runners)
        ext_bundle = self._ingestor.get_features_asof(
            home_team=home, away_team=away,
            asof_ts=bundle.snapshot_ts, market_id=bundle.market_id,
        )
        ext_id = await insert_external_feature_snapshot(conn, ext_bundle)

    self._ext_id_cache[bundle.market_id] = ext_id
    self._ext_bundle_cache[bundle.market_id] = ext_bundle
    return ext_id, ext_bundle
```

### 2.5 Payload A1 e A2

Stessi campi per tutti i runner di un market: il `runner_id` distingue, il modello interpreta la prospettiva tramite `elo_delta` + probabilità implicite del runner.

```python
def _build_a1(self, a0: dict, ext: ExternalFeatureBundle) -> dict:
    return {
        **a0,
        "elo_home": float(ext.elo_home) if ext.elo_home is not None else None,
        "elo_away": float(ext.elo_away) if ext.elo_away is not None else None,
        "elo_delta": float(ext.elo_delta) if ext.elo_delta is not None else None,
        "match_confidence": ext.match_confidence,
    }

def _build_a2(self, a1: dict, ext: ExternalFeatureBundle) -> dict:
    def form_dict(f):
        if f is None:
            return None
        return {
            "points_per_match": f.points_per_match,
            "goal_diff_per_match": f.goal_diff_per_match,
            "win_rate": f.win_rate,
        }
    return {
        **a1,
        "form_home_5":  form_dict(ext.form_home_5),
        "form_away_5":  form_dict(ext.form_away_5),
        "form_home_10": form_dict(ext.form_home_10),
        "form_away_10": form_dict(ext.form_away_10),
    }
```

## 3. Modifica a `ExternalDataIngestor`

Singola estensione: `get_features_asof` aggiunge `history_loaded` a `quality_flags`:

```python
quality_flags = {
    "home_confidence": conf_h,
    "away_confidence": conf_a,
    "resolved_home": resolved_home,
    "resolved_away": resolved_away,
    "history_loaded": self._loaded,  # nuovo
}
```

Nessuna modifica di signature, nessun nuovo metodo, nessun nuovo file. Mantiene l'ingestor "puro lettore" — la scrittura avviene nel FeatureBuilder.

## 4. Persistenza — riassunto

| Tabella | Quando | Quante righe per market in finestra (110min × 6 poll/min, 3 runner) |
|---|---|---|
| `external_feature_snapshots` | 1× alla prima visita del market (se ingestor disponibile) | 1 |
| `feature_vectors` A0 | ogni runner di ogni snapshot | 1980 |
| `feature_vectors` A1 | ogni runner di ogni snapshot (se ingestor disponibile) | 1980 |
| `feature_vectors` A2 | ogni runner di ogni snapshot (se ingestor disponibile) | 1980 |

Totale `feature_vectors` per market: 1980 (no ingestor) o 5940 (con ingestor). Per ~100 markets simultanei in finestra: ~600k righe massimo. Postgres lo gestisce senza problemi a Phase 1.

## 5. Comportamento di degradazione

| Stato | Comportamento |
|---|---|
| `external_ingestor is None` | Solo A0. Nessuna scrittura in `external_feature_snapshots`. `feature_vectors.ext_snapshot_id = NULL`. |
| Ingestor settato, `is_loaded=False` (history mai caricata) | Scrive A1/A2; Elo a default 1500, form `None`. `quality_flags["history_loaded"]=false`. |
| Ingestor settato, team unresolved (`confidence=0.0`) | Scrive A1/A2; `match_confidence="LOW"`, `quality_flags` riporta `home_confidence`/`away_confidence`. |
| Ingestor settato, dati pieni | A1/A2 con valori reali; `match_confidence="HIGH"` se entrambe le team resolution sono >=0.8. |

In tutti i casi A0 viene sempre scritto.

## 6. Test (nuovi e modifiche)

### 6.1 Nuovo file `tests/integration/test_pipeline_a1_a2.py` — 5 test

1. **`test_a0_a1_a2_all_written_with_ingestor_loaded`**
   - 1 market, 3 runner, ingestor con history caricata → 1 poll cycle scrive 9 `feature_vectors` (3 runner × 3 versioni) + 1 `external_feature_snapshot`
   - Asserzione: tutti i 6 record A1/A2 hanno lo stesso `ext_snapshot_id`, gli A0 hanno `ext_snapshot_id=NULL`

2. **`test_only_a0_when_ingestor_is_none`**
   - `FeatureBuilder(pool, external_ingestor=None)` → 3 `feature_vectors` A0, `external_feature_snapshots` vuoto

3. **`test_external_snapshot_cached_per_market`**
   - 3 poll cycle stesso market → `external_feature_snapshots` count == 1; tutti gli A1/A2 (18 righe) hanno lo stesso `ext_snapshot_id`

4. **`test_a1_features_include_elo_a2_includes_form`**
   - Verifica payload JSON: A0 NON contiene chiavi elo/form, A1 contiene `elo_home/elo_away/elo_delta/match_confidence`, A2 contiene anche `form_home_5/form_away_5/form_home_10/form_away_10`
   - I valori non-null per Elo (post-load di history); per form serve almeno 1 match pre-asof

5. **`test_low_confidence_team_match_persists_a1_a2`**
   - Team unresolved → ext_snapshot con `match_confidence='LOW'`, `quality_flags['away_confidence']==0.0`; A1/A2 comunque scritti e linkati

### 6.2 Modifiche ai test esistenti

Nessuna. I test in `test_pipeline_feature_builder.py` passano `external_ingestor=None` o lo omettono (default None), quindi continuano a osservare solo A0 → restano validi.

## 7. Modifiche al codice di produzione

| File | Cambiamento |
|---|---|
| `src/betfair_trading/services/feature_builder.py` | Estensione di `on_market_snapshot`; nuovi metodi privati `_get_or_create_external`, `_load_runner_metadata`, `_extract_teams`, `_build_a1`, `_build_a2`. Tre cache: `_ext_id_cache`, `_ext_bundle_cache`, `_runner_meta_cache`. Import di `Runner` da `models.market` e `insert_external_feature_snapshot` da `db.writer`. |
| `src/betfair_trading/services/external_ingestor.py` | 1 riga: aggiunta di `"history_loaded": self._loaded` a `quality_flags`. |
| `src/betfair_trading/main.py` | Nessun cambiamento (il wiring `FeatureBuilder(pool, ingestor)` esiste già). |

## 8. Vincoli e principi rispettati

- **Anti-leakage**: `asof_ts = bundle.snapshot_ts` è il momento del primo snapshot, mai posteriore al "now" del processo. `EloEngine.get_ratings_asof` e `FormCalculator.compute_form` filtrano `< asof_ts`. Il test #4 e quelli esistenti in `test_pipeline_external_data.py` lo verificano.
- **Audit-first**: scrivere A0 sempre, A1/A2 anche con LOW confidence, persistere `quality_flags` — tutto preserva l'audit completo.
- **Idempotenza**: cache `market_id → ext_snapshot_id` evita scritture multiple in `external_feature_snapshots` per lo stesso market. Nessun `ON CONFLICT` necessario perché `external_feature_snapshots` ha solo `ext_snapshot_id` come PK e non c'è uniqueness su `market_id`.

## 9. Non in questo spec

- Wiring del Decision Engine (consumerà `feature_vectors` ma non è oggetto di questo design).
- Configurazione che permetta di scegliere quale versione "spinge" al model inference (verrà introdotta in Phase 2 piece 2).
- Cache eviction per market scaduti (out-of-window). Le 3 cache crescono in maniera lineare con i markets visti; per Phase 1 (~100 simultanei, qualche centinaio al giorno) non è un problema. Eviction sarà un follow-up se la profondità di tracking cresce.
- Modifiche al `MarketSnapshotBundle` (es. aggiungere runner_meta) — esplicitamente rifiutate a favore del caricamento dalla tabella `runners`.
