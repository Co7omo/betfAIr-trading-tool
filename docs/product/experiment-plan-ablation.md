# Experiment Plan — Ablation (A0/A1/A2)

Cross-reference: `backtesting-protocol.md`, `../architecture/adr-0002-signal-supervised-elo-form.md`.

## Goal
Measure incremental value of external baseline features over market-only signals.

## Ablation Definitions
- **A0**: market features only.
- **A1**: market + Elo features.
- **A2**: market + Elo + form features (N=5 and N=10).

## Controlled Variables
- Same temporal splits.
- Same model family/training budget.
- Same calibration method.
- Same decision/risk policy and costs.

## Hypotheses
1. A1 outperforms A0 on predictive calibration and net ROI stability.
2. A2 improves short-horizon responsiveness vs A1.
3. Gains survive commission/slippage modeling.

## Statistical and Practical Evaluation
- Compare means and dispersion across time blocks.
- Report confidence intervals via block bootstrap by date/event.
- Emphasize practical significance (risk-adjusted net outcomes), not only statistical significance.

## Promotion Criteria (A0 -> A1 -> A2)
- Improvement in calibration and/or log loss.
- No deterioration in max drawdown beyond risk tolerance.
- Net ROI uplift robust across holdout slices.

## Checklist
- [ ] Exactly one changed factor per ablation step.
- [ ] Cost model identical across cohorts.
- [ ] Report includes failure slices, not only aggregate wins.

## References
- Betfair Data Scientists guide
- Betfair Historical Data Services API