# MVP Roadmap

Cross-reference: `01-requirements-nfrs.md`, `09-failure-modes-runbook.md`, `docs/product/experiment-plan-ablation.md`.

## Phase 0 — Foundation
- Finalize scope and ADRs.
- Define data contracts and append-only ledger schema.
- Set observability baseline.

## Phase 1 — Data and Baselines
- Market data ingestion (historical + realtime pre-match).
- External Elo/form ingestion with as-of constraints.
- Build A0 baseline (market-only).

## Phase 2 — Modeling and Decisioning
- Add A1 (market + Elo), then A2 (market + Elo + form).
- Calibration and threshold tuning on out-of-time validation.
- Integrate risk gates and controls.

## Phase 3 — Execution and Reconciliation
- Implement order lifecycle and reconciliation flows.
- Validate idempotency and state consistency.
- Dry-run and paper trading phase.

## Phase 4 — Controlled Live MVP
- Limited bankroll deployment.
- Daily risk review and incident drills.
- KPI tracking and go/no-go for scale-up.

## Exit Criteria (MVP)
- Complete audit chain from features to P&L.
- Stable reconciliation under degraded scenarios.
- Predictive and trading KPIs meet minimum thresholds.
- Runbook drills passed.

## Checklist
- [ ] Each phase has measurable acceptance criteria.
- [ ] No live deployment before reconciliation sign-off.
- [ ] Risk controls verified before enabling capital.

## References
- Betfair Exchange API reference
- Betfair Data Scientists guide