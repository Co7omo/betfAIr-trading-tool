# KPIs and Metrics

Cross-reference: `../architecture/08-observability.md`, `backtesting-protocol.md`.

## KPI Categories
1. Predictive quality.
2. Trading performance.
3. Risk and stability.
4. Operational reliability.

## Predictive Metrics
- Log Loss (multi-class 1X2).
- Brier Score.
- Calibration error (ECE) and reliability curves.

## Trading Metrics
- Net ROI (after commission/slippage).
- Profit factor.
- Hit rate (contextual only; not sole KPI).
- Maximum drawdown.
- Volatility of daily returns.

## Risk Metrics
- Daily stop trigger count.
- Limit breach attempts blocked.
- Exposure concentration by league/event.

## Operational Metrics
- Poll success rate and latency.
- Order placement success and rejection rates.
- Reconciliation lag and mismatch frequency.
- External data freshness and missing rates.

## KPI Governance
- Report metrics by ablation cohort (A0/A1/A2).
- Separate backtest and live/paper-trading dashboards.
- Use rolling windows to detect degradation.

## Checklist
- [ ] Net metrics always include commission and slippage assumptions.
- [ ] Calibration tracked over time, not only aggregate.
- [ ] Risk breach dashboard reviewed daily in MVP phase.

## References
- Betfair Commission documentation
- Betfair Exchange API reference