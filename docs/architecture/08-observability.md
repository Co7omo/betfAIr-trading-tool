# Observability Specification

Cross-reference: `06-data-model.md`, `09-failure-modes-runbook.md`, `docs/product/kpis-and-metrics.md`.

## Objectives
- Detect failures before financial impact escalates.
- Explain every decision and order lifecycle transition.
- Support forensic reconstruction and post-mortem analysis.

## Telemetry Pillars
1. **Logs** (structured, correlated): decision, order, reconciliation, settlement.
2. **Metrics** (time series): latency, error rates, risk flags, P&L drift.
3. **Traces**: end-to-end path from poll cycle to order and reconciliation.

## Minimum Metric Set
- Market polling success rate, p95 latency.
- Decision throughput, approval ratio, rejection reasons.
- Order submission success/failure rate.
- Fill ratio and reconciliation lag.
- Settlement lag and P&L compute lag.
- Daily P&L, drawdown %, stop-loss trigger state.
- External data freshness and missing-rate.
- Model quality monitors (live calibration proxy, drift indicators).

## Alerting (MVP)
- API error burst above threshold.
- Token/auth failures.
- Reconciliation stuck beyond timeout.
- Daily stop-loss reached.
- Kill switch enabled (informational + critical if unexpected).
- External data missing above threshold.
- Model drift alert when calibration degradation persists.

## Logging Contract
All critical logs include:
- `timestamp`
- `market_id`, `event_id`, `decision_id`
- `customerOrderRef` when applicable
- `model_version`, `feature_set_version`
- `config_snapshot_id`
- `outcome` and `reason_code`

## Dashboards (MVP)
1. **Trading Operations Dashboard**: cycle latency, order lifecycle, open exposure.
2. **Risk Dashboard**: risk gate outcomes, stop-loss/kill-switch status.
3. **Model Dashboard**: score distribution, calibration drift proxies.
4. **P&L Dashboard**: intraday and cumulative net performance.

## Checklist
- [ ] Correlation IDs present across logs and traces.
- [ ] Every alert maps to runbook procedure.
- [ ] Dashboard includes pre-match window compliance view.

## References
- Betfair Exchange API reference
- Betfair Data Scientists guide