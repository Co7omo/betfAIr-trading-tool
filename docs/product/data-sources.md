# Data Sources and External Baseline (Option 1)

Cross-reference: `backtesting-protocol.md`, `../architecture/06-data-model.md`, `../architecture/04-c4-component.md`.

## Source Inventory
1. Betfair realtime exchange data (market books/order status).
2. Betfair historical market data.
3. External football results/fixtures data for Elo + form.

## External Baseline: Elo (As-Of, No Leakage)

### Elo Principles
- Maintain team ratings as a stateful timeline.
- For match at time $t$, use latest ratings from matches with completion time < $t$.
- Update ratings only after match completion.

### As-Of Workflow
1. Sort matches chronologically by final whistle timestamp.
2. Before processing match $m_t$, read pre-match Elo state for both teams.
3. Store these values as features for any decision timestamp linked to $m_t$.
4. Apply Elo update after result known.

## External Baseline: Form (N=5, N=10)
- Compute rolling aggregates from the last N completed matches before as-of time.
- Candidate form dimensions: points-per-match, goal differential proxy, win/draw/loss proportions.
- Produce two windows: short (N=5) and medium (N=10).

## Entity Resolution (Provider <-> Betfair)

### Matching Strategy
1. Normalize team names (case, punctuation, abbreviations, locale variants).
2. Match by event date/time proximity within tolerance window.
3. Use competition metadata and home/away ordering.
4. Resolve ambiguous candidates with deterministic tie-break rules.

### Quality Flags
- `match_confidence_score`
- `is_ambiguous_match`
- `requires_manual_review`

## Data Quality Checks
- Freshness SLA checks for external feed.
- Completeness checks for mandatory fields.
- Consistency checks (home/away inversion detection).
- Outlier checks on Elo jumps and form discontinuities.

## Missing Data Policy (MVP)
- If critical mapping missing: block trade decision for that event.
- If non-critical feature missing: use explicit fallback/default and set feature missing flag.
- Always log missing-data reason in decision audit.

## Checklist
- [ ] As-of constraints validated in both train and inference paths.
- [ ] Entity matching confidence logged and queryable.
- [ ] Missing-data handling deterministic and auditable.

## References
- Betfair Data Scientists guide
- Betfair Historical Data Services API
- Betfair Exchange API reference