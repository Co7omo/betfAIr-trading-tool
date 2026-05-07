# Sequence Diagrams

Cross-reference: `03-c4-container.md`, `04-c4-component.md`, `06-data-model.md`.

## (a) Market Discovery + Pre-Match Polling

```mermaid
sequenceDiagram
    participant SCH as Scheduler
    participant MDC as Market Data Collector
    participant BF as Betfair API
    participant FB as Feature Builder
    participant AUD as Ledger/Audit

    SCH->>MDC: Trigger discovery cycle
    MDC->>BF: listMarketCatalogue (football, Match Odds, pre-match)
    BF-->>MDC: market list
    MDC->>AUD: Append market_discovery_event

    loop Every 10s within T-120..T-10
      SCH->>MDC: Trigger polling
      MDC->>BF: listMarketBook(market_id)
      BF-->>MDC: best prices, volumes, status
      MDC->>FB: market_snapshot_bundle
      MDC->>AUD: Append market_snapshot_event
    end
```

## (b) Decision -> placeOrders -> Reconciliation

```mermaid
sequenceDiagram
    participant FB as Feature Builder
    participant MI as Model Inference
    participant DE as Decision Engine
    participant EX as Execution Engine
    participant BF as Betfair API
    participant AUD as Ledger/Audit

    FB->>MI: feature_vector + feature_set_version
    MI-->>DE: p_home, p_draw, p_away + model_version
    DE->>DE: compute p_market, edge_net, risk filters
    DE->>AUD: Append decision_event (decision_id)

    alt Decision approved
      DE->>EX: trade_intent(decision_id, side, stake, price)
      EX->>BF: placeOrders(customerOrderRef)
      BF-->>EX: instructionReports/place status
      EX->>AUD: Append order_submission_event

      loop Reconciliation
        EX->>BF: listCurrentOrders(customerOrderRef)
        BF-->>EX: current order/fill status
        EX->>AUD: Append order_reconciliation_event
      end
    else Decision rejected
      DE->>AUD: Append decision_rejected_event(reason)
    end
```

## (c) Settlement -> P&L -> Bankroll Update

```mermaid
sequenceDiagram
    participant EX as Execution/Settlement Reconciler
    participant BF as Betfair API
    participant PE as P&L Engine
    participant AUD as Ledger/Audit
    participant CFG as Bankroll/Config

    EX->>BF: listClearedOrders(market/event scope)
    BF-->>EX: settled bets and cleared profits
    EX->>PE: settlement_events + fills
    PE->>PE: compute per-order and per-market P&L
    PE->>AUD: Append pnl_event
    PE->>CFG: update bankroll snapshot
    CFG->>AUD: Append bankroll_snapshot_event
```

## Checklist
- [ ] Sequence (a) enforces pre-match time guards.
- [ ] Sequence (b) always writes decision before order submission.
- [ ] Sequence (c) uses clearing data as source of truth for realized P&L.

## References
- Betfair Exchange API reference (`listMarketCatalogue`, `listMarketBook`, `placeOrders`, `listCurrentOrders`, `listClearedOrders`)
- Betfair Data Scientists guide