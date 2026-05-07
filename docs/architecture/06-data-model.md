# Data Model (Audit-First, Append-Only)

Cross-reference: `05-sequence-diagrams.md`, `08-observability.md`, `docs/product/backtesting-protocol.md`.

## Design Principles
- Append-only event logs for all critical lifecycle entities.
- Immutable records with explicit event timestamps.
- Strong IDs for traceability across decisions, orders, fills, and P&L.

## Core Entities / Tables

1. `markets`
   - Keys: `market_id` (PK), `event_id`, `sport_id`, `market_type`, `start_time`.
   - Attributes: competition, country, market status.

2. `runners`
   - Keys: (`market_id`, `runner_id`) composite PK.
   - Attributes: runner name, selection metadata.

3. `market_snapshots` (append-only)
   - Keys: `snapshot_id` (PK), `market_id`, `snapshot_ts`.
   - Attributes: best back/lay, spread, available volume, traded volume.

4. `external_feature_snapshots` (append-only)
   - Keys: `ext_snapshot_id` (PK), `event_key`, `asof_ts`.
   - Attributes: Elo home/away, Elo delta, form N=5/N=10, quality flags.

5. `feature_vectors` (append-only)
   - Keys: `feature_vector_id` (PK), `decision_id` (unique), `feature_set_version`.
   - Attributes: serialized feature payload hash, generation timestamp.

6. `model_inferences` (append-only)
   - Keys: `inference_id` (PK), `decision_id`, `model_version`.
   - Attributes: `p_home`, `p_draw`, `p_away`, calibration metadata.

7. `decisions` (append-only)
   - Keys: `decision_id` (PK), `market_id`, `event_id`, `decision_ts`.
   - Attributes: `p_market_*`, `edge_gross_*`, `edge_net_*`, threshold checks, risk checks, decision outcome, rationale.

8. `orders` (append-only lifecycle events)
   - Keys: `order_event_id` (PK), `customerOrderRef`, `decision_id`.
   - Attributes: side, price, size, status transitions, API response summary.

9. `fills` (append-only)
   - Keys: `fill_id` (PK), `customerOrderRef`, `fill_ts`.
   - Attributes: matched size, avg price, remaining size.

10. `settlements` (append-only)
    - Keys: `settlement_id` (PK), `customerOrderRef`, `market_id`.
    - Attributes: cleared profit/loss, commission, settled timestamp.

11. `pnl_events` (append-only)
    - Keys: `pnl_event_id` (PK), `market_id`, `event_id`, `calc_ts`.
    - Attributes: gross P&L, commission, net P&L, cumulative daily P&L.

12. `bankroll_snapshots` (append-only)
    - Keys: `bankroll_snapshot_id` (PK), `snapshot_ts`.
    - Attributes: bankroll value, available balance proxy, daily drawdown, stop status.

13. `config_snapshots` (append-only)
    - Keys: `config_snapshot_id` (PK), `effective_ts`.
    - Attributes: threshold `θ`, liquidity/spread filters, risk params, kill-switch state.

## Key Identifiers
- `market_id`, `event_id`, `runner_id`: market topology.
- `decision_id`: root correlation key for decision lifecycle.
- `customerOrderRef`: order-level idempotency and reconciliation key.
- `model_version`, `feature_set_version`: reproducibility anchors.

## Retention Policy (MVP)
- Audit-critical tables: long retention (minimum multi-year according to operational/legal needs).
- High-frequency snapshots: tiered retention (hot storage + cold archive).
- Derived aggregates may be recomputed; raw events are source-of-truth.

## Service Contract Versioning
- Every inter-service payload includes:
  - `schema_version`
  - `generated_at`
  - correlation IDs (`decision_id`, `market_id`, etc.)
- Breaking changes require new contract version and migration note in ADR/update log.

## Checklist
- [ ] Decision-to-order chain is always joinable via `decision_id` and `customerOrderRef`.
- [ ] Model and feature versions persist for every decision.
- [ ] No in-place updates in audit event tables.

## References
- Betfair Exchange API reference
- Betfair Historical Data Services API