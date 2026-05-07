# Failure Modes and Runbook

Cross-reference: `07-risk-controls.md`, `08-observability.md`, `10-mvp-roadmap.md`.

## Incident Catalogue
1. Betfair API unavailable/degraded.
2. Auth token expired/invalid.
3. Order stuck in pending/unknown state.
4. Decision-execution state mismatch.
5. External Elo/form data missing or stale.
6. Model drift or calibration degradation.
7. Ledger write failure.

## Standard Operating Procedure (SOP)

### A. Safe-Stop
1. Enable kill switch (block new entries).
2. Keep reconciliation loop active for outstanding orders.
3. Snapshot system state and active config.
4. Export incident audit bundle.

### B. Reconciliation Recovery
1. Pull current orders and cleared orders.
2. Rebuild order state timeline by `customerOrderRef`.
3. Resolve orphan decision/order links.
4. Recompute P&L deltas and bankroll snapshot.

### C. External Data Degradation
1. If stale beyond threshold: block decisions dependent on stale features.
2. Optionally downgrade to market-only mode only if explicitly enabled in config and documented.
3. Emit degraded-mode alerts.

### D. Drift Handling
1. Trigger warning state.
2. Tighten threshold `θ` or reduce sizing via config.
3. Escalate to manual review and potential model rollback.

## Post-Mortem Template
- Incident ID
- Timeline (detection, mitigation, recovery)
- Blast radius (markets, orders, financial impact)
- Root cause
- Corrective and preventive actions
- Validation evidence
- Owner and due date

## Audit Export Bundle (Minimum)
- Decision events
- Order submission/reconciliation events
- Fills and settlements
- P&L events and bankroll snapshots
- Config snapshots and kill-switch transitions

## Checklist
- [ ] Every alert has a linked SOP section.
- [ ] Recovery tested on at least one synthetic incident per category.
- [ ] Post-mortem actions tracked to closure.

## References
- Betfair Exchange API reference
- Betfair Commission documentation