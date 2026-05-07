# Risk Controls

Cross-reference: `04-c4-component.md`, `09-failure-modes-runbook.md`, `docs/product/kpis-and-metrics.md`.

## Risk Objectives
- Prevent catastrophic losses.
- Bound exposure per event/market/day.
- Ensure deterministic emergency stop.

## Position Sizing (MVP Default)
- Base sizing policy: fractional Kelly with multiplier 0.25.
- Hard cap: max 2% bankroll per market.
- Max 1 position per event.

## Hard Limits
1. **Time Window**: only T-120 to T-10.
2. **Liquidity Filter**: minimum available liquidity threshold required.
3. **Spread Filter**: maximum spread threshold allowed.
4. **Daily Stop**: halt new entries if daily drawdown reaches 5% bankroll.
5. **Kill Switch**: operator-triggered immediate block of new orders.

## Policy Gates (Order of Evaluation)
1. Pre-match time guard.
2. Data quality guard (market and external data completeness).
3. Edge threshold (`edge_net >= θ`).
4. Liquidity and spread checks.
5. Position/risk limits.
6. Operational health checks (API/latency/degraded mode).

## Control Outcomes
- **ALLOW**: submit order intent.
- **BLOCK_SOFT**: skip this cycle; record reason.
- **BLOCK_HARD**: suspend trading until manual intervention.

## Risk Audit Requirements
Each decision stores:
- edge decomposition (gross, commission, slippage, net)
- risk checks and pass/fail reason
- active config snapshot (limits and thresholds)

## Checklist
- [ ] Daily stop tested with simulated drawdown.
- [ ] Kill switch state visible in dashboards and audit logs.
- [ ] Position cap and one-position-per-event constraint enforced atomically.

## References
- Betfair Commission documentation
- Betfair Exchange API reference