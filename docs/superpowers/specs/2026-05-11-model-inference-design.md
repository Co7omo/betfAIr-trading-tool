# Model Inference Baseline — Design

**Data:** 2026-05-11
**Scope:** Sostituire il `BiasedStubProvider` con un vero `ModelInferenceProvider` che carica un modello supervised LogisticRegression calibrato e persiste `model_inferences` linkate alle `decisions`. Include il training pipeline CLI (su CSV storico, feature Elo+form) e le tabelle `model_versions`/`model_inferences` + audit linkage via `decisions.inference_id`.
**Out of scope:** Market features (no historical market quotes nel dataset), backtest harness, evaluation suite (calibration plots), hot reload, A/B testing fra modelli.

## 1. Obiettivo

Phase 2 ha introdotto la Decision Engine che consuma probabilità via `ProbabilityProvider` Protocol. Per ora il provider è stub (`MarketImpliedProvider`, `BiasedStubProvider`). Questa iterazione cabla un vero modello supervised baseline:

- Training pipeline offline su CSV match storici (football-data format): label = FTR (H/D/A); feature = Elo + form as-of pre-kickoff.
- Modello: `LogisticRegression(multi_class='multinomial')` calibrato con Platt (`CalibratedClassifierCV(method='sigmoid', cv=5)`).
- Artifact persistito come joblib su filesystem (`models/`) + metadata in `model_versions`.
- Inference live: `ModelInferenceProvider` carica il latest model a boot, legge l'A2 feature_vector dal DB, predice, persiste `model_inferences`, ritorna `(probs, inference_id)`.
- Decision: `decisions.inference_id` linka la decisione all'inferenza per audit completo.

Vincoli mantenuti:
- **Anti-leakage**: training e inference usano `EloEngine.get_ratings_asof()` / `FormCalculator.compute_form()` che filtrano `< asof_ts`. DatasetBuilder applica il risultato del match SOLO dopo aver letto le feature.
- **Zero skew train/serve**: `build_feature_dict(values)` è il single source of truth, chiamato sia da `DatasetBuilder` (training) che da `ModelInferenceProvider` (inference). Test unit dedicato lock-a l'invariante.
- **Audit chain**: `decisions.inference_id → model_inferences → model_versions → file_path` joinable end-to-end.
- **Reproducibility**: `training_data_hash` (SHA256 CSV) + `training_params` + `feature_names` persistiti in `model_versions`.

## 2. Architettura

### 2.1 Decomposizione

Nuovo sotto-package `src/betfair_trading/training/` + nuovo file in `services/`:

```
src/betfair_trading/
├── training/                          # NUOVO
│   ├── __init__.py
│   ├── features.py                    # FEATURE_NAMES + build_feature_dict (shared)
│   ├── dataset.py                     # DatasetBuilder: replay temporale CSV
│   └── train.py                       # CLI entrypoint async
└── services/
    └── model_inference_provider.py    # NUOVO — implementa ProbabilityProvider
```

E modifiche minori in:
- `models/inference.py` (NUOVO): `ModelVersion`, `ModelInference` Pydantic
- `models/decision.py`: + `inference_id: UUID | None`
- `db/writer.py`: + `insert_model_version`, `insert_model_inference`; update `insert_decision`
- `services/probability_providers.py`: Protocol cambia signature, stubs aggiornati
- `services/decision_engine.py`: unpack `(probs, inference_id)`, salva in Decision
- `main.py`: sostituisce `BiasedStubProvider` con `ModelInferenceProvider`
- `pyproject.toml`: + scikit-learn, numpy, joblib
- `.gitignore`: + `models/*.joblib`
- `models/.gitkeep`: nuovo file

### 2.2 ProbabilityProvider Protocol — breaking change

Vecchia signature:
```python
async def get_probabilities(...) -> dict[int, float]: ...
```

Nuova signature:
```python
async def get_probabilities(...) -> tuple[dict[int, float], UUID | None]: ...
```

Gli stub esistenti (`MarketImpliedProvider`, `BiasedStubProvider`) ritornano `(probs, None)`. `ModelInferenceProvider` ritorna `(probs, inference_id)`. Il `DecisionEngine.evaluate()` unpacka e salva `inference_id` nel `Decision`.

### 2.3 Feature schema — zero skew

`training/features.py` espone:

```python
FEATURE_NAMES: list[str] = [
    "elo_home", "elo_away", "elo_delta",
    "form_home_5_ppm", "form_away_5_ppm",
    "form_home_5_gd",  "form_away_5_gd",
    "form_home_5_wr",  "form_away_5_wr",
    "form_home_10_ppm","form_away_10_ppm",
    "form_home_10_gd", "form_away_10_gd",
    "form_home_10_wr", "form_away_10_wr",
]

def build_feature_dict(values: dict[str, float | None]) -> dict[str, float]:
    """Normalize a feature values dict to FEATURE_NAMES order, replacing None with 0.0.
    Auto-computes elo_delta when elo_home/elo_away present and elo_delta absent.
    """
    # ...

def feature_dict_to_array(d: dict[str, float]) -> np.ndarray:
    """Shape (1, len(FEATURE_NAMES)) in FEATURE_NAMES order."""
    return np.array([[d[name] for name in FEATURE_NAMES]])
```

Sia il training (dataset builder, da `FormFeatures` objects) sia l'inference (da A2 JSONB dict) costruiscono il loro `values: dict[str, float | None]` e lo passano a `build_feature_dict`. Stessa funzione → output identico. Test unit `test_zero_skew_train_vs_inference_extraction` lock-a questa invariante.

### 2.4 Training pipeline

**`training/dataset.py` — `DatasetBuilder`**

```python
class DatasetBuilder:
    """Replay temporale del CSV: per ogni match calcola feature as-of pre-kickoff,
    poi applica il risultato per il match successivo.
    """
    def __init__(self, k_factor: float = 20.0, initial_rating: float = 1500.0):
        self.elo = EloEngine(k_factor=k_factor, initial_rating=initial_rating)
        self.form = FormCalculator()

    def build(self, csv_path: Path) -> tuple[np.ndarray, np.ndarray, list[datetime]]:
        """Returns (X, y, dates). X shape: (n, len(FEATURE_NAMES)). y: int [0=H, 1=D, 2=A]."""
```

Workflow interno:
1. Parse CSV (riusa `_parse_date` di `ExternalDataIngestor`)
2. Sort cronologicamente
3. Per ogni match (dt, home, away, result, fthg, ftag):
   - Lettura: `elo_h, elo_a = elo.get_ratings_asof(home, away, dt)`; `form_*` via `form.compute_form(team, dt, n)`
   - `values = {"elo_home": elo_h, "elo_away": elo_a, "form_home_5_ppm": form_h5.points_per_match if form_h5 else None, ...}`
   - `d = build_feature_dict(values)`
   - Append `[d[name] for name in FEATURE_NAMES]` a `X_rows`
   - Append `{"H":0,"D":1,"A":2}[result]` a `y_rows`
   - **DOPO** la lettura: `elo.apply_result(home, away, result, dt)`; `form.add_match(home, away, result, fthg, ftag, dt)`

L'ordine "leggi prima, applica dopo" è il garante anti-leakage. Test esplicito `test_build_anti_leakage` lo verifica.

**`training/train.py` — CLI entrypoint**

```bash
uv run python -m betfair_trading.training.train \
    --csv-path data/results.csv \
    --model-name logistic_v1 \
    --output-dir models/ \
    --test-size 0.2
```

Workflow:
1. Parse args; validate paths
2. `X, y, dates = DatasetBuilder().build(csv_path)`
3. **Temporal split**: `split_idx = int(len(X) * (1 - args.test_size))`; `X_train, X_test = X[:split_idx], X[split_idx:]` (NOT random)
4. Pipeline:
   ```python
   base = LogisticRegression(multi_class='multinomial', max_iter=1000, C=1.0)
   pipe = Pipeline([("scaler", StandardScaler()), ("clf", base)])
   model = CalibratedClassifierCV(pipe, method='sigmoid', cv=5)
   ```
5. `model.fit(X_train, y_train)`
6. Eval on X_test:
   - `metrics["log_loss"] = log_loss(y_test, model.predict_proba(X_test))`
   - `metrics["accuracy"] = accuracy_score(y_test, model.predict(X_test))`
   - `metrics["brier_home/draw/away"] = brier_score_loss((y_test == i).astype(int), proba[:, i])`
   - `metrics["confusion_matrix"] = confusion_matrix(y_test, predictions).tolist()`
7. Save artifact: `joblib.dump(model, f"{output_dir}/{model_name}_{timestamp}.joblib")` con `timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")`
8. Hash CSV: `training_data_hash = sha256(csv_path.read_bytes()).hexdigest()`
9. INSERT in `model_versions`:
   - Async script: `await create_pool(os.environ["DATABASE_URL"])` poi `await insert_model_version(conn, ModelVersion(...))`
10. Print summary metrics

Failure modes:
- CSV non esistente / non valido → exit 1, error log
- < 100 samples → warn ma procedi (utile per testing)
- DB unreachable → exit 1; l'artifact joblib è già salvato e può essere reinserito manualmente

Dipendenze nuove:
```toml
"scikit-learn>=1.5.0",
"numpy>=2.0.0",
"joblib>=1.4.0",
```

### 2.5 Inference adapter — `services/model_inference_provider.py`

```python
class ModelInferenceProvider:
    def __init__(self, pool: asyncpg.Pool, models_dir: str = "models/"):
        self._pool = pool
        self._models_dir = Path(models_dir)
        self._model = None
        self._model_version_id: UUID | None = None
        self._model_name: str = "STUB_NO_MODEL"
        self._fallback = MarketImpliedProvider()

    @property
    def model_version(self) -> str:
        return self._model_name

    async def initialize(self) -> None:
        """Load latest model_version + joblib. Called once at main() startup.
        On miss: log warning, leave _model None (fallback kicks in at inference time)."""

    async def get_probabilities(
        self,
        bundle: MarketSnapshotBundle,
        runners: list[Runner],
        feature_vector_ids: list[uuid.UUID],
    ) -> tuple[dict[int, float], UUID | None]:
        # 1. if self._model is None → return (await self._fallback.get_probabilities(...))[0], None
        # 2. SELECT features FROM feature_vectors WHERE feature_vector_id = ANY($1)
        #    AND feature_set_version = 'A2' LIMIT 1
        # 3. If no row → fallback (no A2 means ingestor is None → can't infer)
        # 4. a2_features = json.loads(row["features"]) if str else row["features"]
        # 5. values = _extract_values_from_a2(a2_features)
        # 6. feature_dict = build_feature_dict(values)
        # 7. X = feature_dict_to_array(feature_dict)
        # 8. proba = self._model.predict_proba(X)[0]  # [p_h, p_d, p_a]
        # 9. Map outcomes to runner_id via sort_priority:
        #    sorted_r = sorted(runners, key=lambda r: (r.sort_priority is None, r.sort_priority))
        #    result_probs = {sorted_r[0].runner_id: float(proba[0]),
        #                    sorted_r[1].runner_id: float(proba[1]),
        #                    sorted_r[2].runner_id: float(proba[2])}
        # 10. INSERT model_inferences row, get inference_id
        # 11. Return (result_probs, inference_id)
```

Helper `_extract_values_from_a2(a2: dict) -> dict[str, float | None]`:

```python
def _extract_values_from_a2(a2: dict) -> dict[str, float | None]:
    fh5  = a2.get("form_home_5")  or {}
    fa5  = a2.get("form_away_5")  or {}
    fh10 = a2.get("form_home_10") or {}
    fa10 = a2.get("form_away_10") or {}
    return {
        "elo_home": a2.get("elo_home"),
        "elo_away": a2.get("elo_away"),
        "elo_delta": a2.get("elo_delta"),
        "form_home_5_ppm": fh5.get("points_per_match"),
        "form_away_5_ppm": fa5.get("points_per_match"),
        "form_home_5_gd":  fh5.get("goal_diff_per_match"),
        "form_away_5_gd":  fa5.get("goal_diff_per_match"),
        "form_home_5_wr":  fh5.get("win_rate"),
        "form_away_5_wr":  fa5.get("win_rate"),
        "form_home_10_ppm": fh10.get("points_per_match"),
        "form_away_10_ppm": fa10.get("points_per_match"),
        "form_home_10_gd":  fh10.get("goal_diff_per_match"),
        "form_away_10_gd":  fa10.get("goal_diff_per_match"),
        "form_home_10_wr":  fh10.get("win_rate"),
        "form_away_10_wr":  fa10.get("win_rate"),
    }
```

### 2.6 Wiring in `main.py`

Sostituisce:
```python
provider = BiasedStubProvider(home_bias=0.05)
```

con:
```python
provider = ModelInferenceProvider(pool=pool, models_dir="models/")
await provider.initialize()  # blocca finché modello caricato (o warn + fallback)
```

Tutto il resto resta invariato (`DecisionEngine` consuma il provider via Protocol).

### 2.7 Decision Engine — unpack tuple

In `evaluate()`:
```python
# prima
p_model = await self._provider.get_probabilities(...)
# dopo
p_model, inference_id = await self._provider.get_probabilities(...)
```

Nella costruzione del `Decision`:
```python
decision = Decision(
    ...,
    inference_id=inference_id,
    ...
)
```

## 3. Schema `model_versions`, `model_inferences`, ALTER `decisions`

Migrazione `alembic/versions/003_model_versions_inferences.py`:

```sql
CREATE TABLE model_versions (
    model_version_id     UUID         NOT NULL DEFAULT uuid_generate_v4(),
    model_name           TEXT         NOT NULL,
    feature_set_version  TEXT         NOT NULL,
    created_ts           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    file_path            TEXT         NOT NULL,
    training_data_hash   TEXT         NOT NULL,
    training_csv_path    TEXT         NOT NULL,
    training_params      JSONB        NOT NULL DEFAULT '{}',
    metrics              JSONB        NOT NULL DEFAULT '{}',
    feature_names        JSONB        NOT NULL,
    n_train              INT          NOT NULL,
    n_test               INT          NOT NULL,
    PRIMARY KEY (model_version_id)
);
CREATE INDEX idx_model_versions_created ON model_versions (created_ts DESC);

CREATE TABLE model_inferences (
    inference_id         UUID         NOT NULL DEFAULT uuid_generate_v4(),
    model_version_id     UUID         NOT NULL,
    market_id            TEXT         NOT NULL,
    event_id             TEXT         NOT NULL,
    inference_ts         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    asof_ts              TIMESTAMPTZ  NOT NULL,
    p_home               NUMERIC(8,6),
    p_draw               NUMERIC(8,6),
    p_away               NUMERIC(8,6),
    feature_vector_ids   UUID[]       NOT NULL,
    features_used        JSONB        NOT NULL,
    PRIMARY KEY (inference_id)
);
CREATE INDEX idx_model_inferences_market ON model_inferences (market_id, inference_ts);
CREATE INDEX idx_model_inferences_version ON model_inferences (model_version_id);

ALTER TABLE decisions ADD COLUMN inference_id UUID;
CREATE INDEX idx_decisions_inference ON decisions (inference_id) WHERE inference_id IS NOT NULL;
```

`clean_db` autouse fixture in `tests/integration/conftest.py` aggiunge `model_versions` e `model_inferences` al TRUNCATE.

## 4. Pydantic contracts — `models/inference.py`

```python
class ModelVersion(BaseModel):
    model_version_id: UUID = Field(default_factory=uuid4)
    model_name: str
    feature_set_version: str  # "A2_EXT_ONLY"
    created_ts: datetime | None = None  # DB default NOW()
    file_path: str
    training_data_hash: str
    training_csv_path: str
    training_params: dict
    metrics: dict
    feature_names: list[str]
    n_train: int
    n_test: int


class ModelInference(BaseModel):
    inference_id: UUID = Field(default_factory=uuid4)
    model_version_id: UUID
    market_id: str
    event_id: str
    inference_ts: datetime | None = None  # DB default
    asof_ts: datetime
    p_home: Decimal
    p_draw: Decimal
    p_away: Decimal
    feature_vector_ids: list[UUID]
    features_used: dict[str, float]
```

E modifica a `models/decision.py`:
```python
class Decision(BaseModel):
    ...
    inference_id: UUID | None = None  # NUOVO
```

## 5. DB writer additions in `db/writer.py`

```python
async def insert_model_version(conn: asyncpg.Connection, mv: ModelVersion) -> UUID
async def insert_model_inference(conn: asyncpg.Connection, mi: ModelInference) -> UUID
```

E `insert_decision` viene esteso per includere la nuova colonna `inference_id` (colonna #18 nell'INSERT).

## 6. Test strategy

### 6.1 Unit tests (no DB)

**`tests/unit/test_training_features.py`** (5 test):
1. `test_build_feature_dict_complete_values` — tutti i valori popolati → output corretto
2. `test_build_feature_dict_none_replaced_with_zero` — None → 0.0
3. `test_build_feature_dict_elo_delta_auto_computed` — elo_home/away present, delta absent → delta = home - away
4. `test_feature_dict_to_array_shape_and_order` — shape (1, 15), ordine FEATURE_NAMES
5. `test_zero_skew_train_vs_inference_extraction` — values dict da training (scalari) e da A2 JSON producono lo stesso output `build_feature_dict`

**`tests/unit/test_dataset_builder.py`** (3 test):
1. `test_build_emits_n_rows_for_n_matches` — 4 match → X.shape == (4, 15), y.shape == (4,)
2. `test_build_anti_leakage` — 4 match cronologici Liverpool vs Arsenal; verifica che la riga del 4° match NON includa l'esito del 4° match (Elo as-of vs Elo post-update sono diversi solo dopo l'apply)
3. `test_build_labels_mapped_correctly` — H→0, D→1, A→2

### 6.2 Integration tests

**`tests/integration/test_model_inference_provider.py`** (5 test):
1. `test_initialize_loads_latest_model` — seed 2 `model_versions` con joblib su disco; `provider.initialize()`; `provider.model_version == latest.model_name`
2. `test_initialize_no_model_falls_back` — DB vuoto → `provider.model_version == "STUB_NO_MODEL"`; `get_probabilities` ritorna market-implied + None
3. `test_get_probabilities_persists_inference` — modello caricato + A2 in DB → ritorna `(probs, inference_id)`; `model_inferences` ha 1 riga consistente
4. `test_get_probabilities_falls_back_when_no_a2` — modello caricato MA solo A0 nei feature_vectors → market-implied + None; `model_inferences` count == 0
5. `test_decision_links_inference_id` — full pipeline → `decisions.inference_id` non-NULL e linkato a `model_inferences.inference_id`

**`tests/integration/test_train_cli.py`** (2 test):
1. `test_train_end_to_end` — CSV fixture (~50 match); invoca `python -m betfair_trading.training.train`; verifica joblib esiste, `model_versions` ha riga, hash CSV matcha
2. `test_train_temporal_split_respected` — CSV con 100 match, test_size=0.2 → `n_train == 80`, `n_test == 20`

### 6.3 Modifiche ai test esistenti

- `tests/unit/test_probability_providers.py`: aggiornare i 2 test esistenti per unpack del tuple `(probs, None)`
- `tests/integration/test_pipeline_decision.py`: aggiornare le asserzioni se necessario (es. `inference_id` può essere NULL nel path stub)
- `tests/integration/conftest.py`: estendere TRUNCATE con `model_versions, model_inferences`
- `tests/integration/test_pg_smoke.py`: aggiungere `model_versions, model_inferences` al set `expected`

Nessuna modifica al codice di produzione esistente al di fuori delle 4 modifiche elencate in §2.1 (Decision Engine + main + decision.py + writer.py).

## 7. Vincoli di correttezza

- **Anti-leakage at training time**: DatasetBuilder applica `elo.apply_result()` DOPO aver letto le feature, mai prima. Test esplicito.
- **Zero skew train/serve**: `build_feature_dict(values)` è il single point of truth. Test esplicito.
- **Audit chain**: `decisions.inference_id → model_inferences → model_versions → file_path`. Test esplicito.
- **Reproducibility**: `training_data_hash` (SHA256) garantisce che un retrain identico dia sempre lo stesso modello (deterministico data la stessa CSV).
- **Append-only**: nessuna modifica alle tabelle esistenti se non `ALTER decisions ADD COLUMN inference_id`. Le nuove tabelle sono solo INSERT.
- **Fallback graceful**: se manca il modello o l'A2 feature_vector, `ModelInferenceProvider` cade su `MarketImpliedProvider` (edge=0). Tutti i livelli del sistema continuano a funzionare; le decisioni saranno BLOCK_SOFT su `edge_threshold`.

## 8. Non in questo spec

- **Market features nel training set** — richiede backfill da Betfair Historical Data API (>1 settimana di lavoro). Quando le feature_vectors live si accumuleranno (~3 mesi di runtime), retrainare con A2 completo incluso market.
- **Backtest harness** — replay storico di decisions su match passati per misurare hit rate/ROI/calibration. Follow-up indipendente.
- **Model evaluation suite** — calibration plots, reliability diagrams, slicing per competition/team. Phase 3.
- **Hot reload del modello** — il main carica una sola volta a startup. Per swap: redeploy. Phase 3.
- **A/B testing fra model_versions** — più modelli attivi in parallelo. Phase 3+.
- **Online learning / retraining incrementale** — out of scope. Il workflow è batch offline.
