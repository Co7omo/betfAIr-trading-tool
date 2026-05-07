# Backtesting Protocol

Cross-reference: `experiment-plan-ablation.md`, `../architecture/06-data-model.md`, `../architecture/07-risk-controls.md`.

## Objective
Evaluate predictive and trading performance with realistic constraints and strict anti-leakage controls.

## Data Scope
- Betfair historical pre-match data for Match Odds 1X2.
- External results data for Elo and form computation.
- Only observations within default pre-match window T-120 to T-10.

## Temporal Split Strategy
- Strict out-of-time splits:
  - Train: older interval.
  - Validation: subsequent interval.
  - Test: most recent holdout interval.
- No random shuffling across time.

## Anti-Leakage Rules
1. Feature computation strictly as-of timestamp.
2. Elo updates only with matches completed before as-of.
3. Form windows (N=5, N=10) built only from prior completed matches.
4. No target leakage through future market or result information.

## Execution Simulation Assumptions
- Polling cadence: 10s snapshots.
- Entry eligibility: edge and risk rules identical to live MVP.
- Fill model:
  - Base fill probability as function of quoted liquidity/spread regime.
  - Slippage penalty modeled by market depth proxy.

## Cost Model
- Commission modeled per official Betfair policy.
- Slippage estimated as function of spread + available volume + order aggressiveness.
- Net P&L = gross P&L − commission − slippage.

## Evaluation Metrics
### Predictive
- Log Loss, Brier Score, ECE.

### Trading
- Net ROI, max drawdown, profit factor, daily return distribution.

## Robustness Checks
- Regime slices by league tier/liquidity bucket.
- Sensitivity to threshold `θ`.
- Sensitivity to slippage assumptions.

## Checklist
- [ ] Temporal split boundaries documented.
- [ ] Feature pipelines validated for as-of correctness.
- [ ] Cost model assumptions explicitly versioned.
- [ ] Same decision policy in backtest and live config baseline.

## References
- Betfair Historical Data Services API
- Betfair Exchange API reference
- Betfair Commission documentation