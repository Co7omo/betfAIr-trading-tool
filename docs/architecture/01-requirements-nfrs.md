# Requirements and NFRs

## Scope Baseline
See `00-overview.md` and `adr-0001-scope-prematch-only.md`.

## Functional Requirements (MVP)
1. **Market discovery**
   - Discover football Match Odds 1X2 markets using catalog metadata.
   - Restrict to PRE-MATCH markets only.
2. **Market polling**
   - Poll order book and traded volume every 10s (default MVP).
   - Operate only in window T-120 to T-10 (default MVP).
3. **External data ingestion**
   - Ingest Elo ratings and recent form features computed as-of decision timestamp.
4. **Feature building**
   - Build reproducible feature vectors with explicit `feature_set_version`.
5. **Model inference**
   - Produce calibrated probabilities: `p_home`, `p_draw`, `p_away`.
6. **Decision policy**
   - Compute market-implied probabilities and `edge_net`.
   - Trade only when thresholds and risk constraints pass.
7. **Execution**
   - Submit orders with stable `customerOrderRef`.
   - Reconcile order status, fills, cancellations, and remaining exposure.
8. **Post-trade lifecycle**
   - Record settlement and compute per-order and per-market P&L.
   - Update bankroll snapshots.
9. **Audit trail**
   - Persist decisions, features, model output, orders, fills, settlement events, and bankroll snapshots as immutable records.
10. **Control plane**
   - Runtime config + kill switch + daily stop-loss enforcement.

## Non-Functional Requirements

### Reliability
- Target availability during pre-match windows.
- Safe-stop path must prevent new orders and preserve reconciliation.

### Auditability and Compliance
- End-to-end traceability from market snapshot to final P&L.
- Append-only audit records, time-stamped, versioned, queryable.

### Performance
- End-to-end decision latency per polling cycle bounded by polling interval.
- Backpressure control when API calls slow down.

### Security
- Secret isolation for API credentials and tokens.
- Least privilege and rotation policy.

### Observability
- Full logs, metrics, and alerts for decision, execution, and risk states.

### Reproducibility
- Every decision links to model artifact version + feature set version + config hash.

## MVP Defaults (Explicit)
- Trading window: T-120 → T-10.
- Polling: 10s.
- Risk sizing: fractional Kelly 0.25.
- Position cap: 2% bankroll per market.
- Daily stop: 5% bankroll drawdown.
- Max positions: 1 per event.

## Acceptance Checklist
- [ ] Pre-match guardrails active in discovery and decision paths.
- [ ] No order placed outside T-120/T-10.
- [ ] Decision record always precedes order submission.
- [ ] Reconciliation closes all order states by market settlement.
- [ ] Daily stop-loss and kill switch verified in dry-run.

## References
- Betfair Data Scientists guide
- Betfair Exchange API reference (`listMarketCatalogue`, `listMarketBook`, `placeOrders`, `listCurrentOrders`, `listClearedOrders`)
- Betfair Historical Data Services API
- Betfair Commission documentation