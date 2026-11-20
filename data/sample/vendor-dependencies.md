---
title: Third-Party Vendor Dependencies
kind: architecture
owner: acme-platform-team
doc_date: 2024-06-14
---
# Third-Party Vendor Dependencies

## Card processing

All card authorisations are routed to a single acquiring processor, NorthPay. There is no secondary processor
and no automatic failover path. The NorthPay contract guarantees 99.9% monthly availability with service credits
capped at 5% of monthly fees. The contract renews in March 2025.

## Fraud signals

fraud-scorer enriches transactions with device signals from SignalHive. If SignalHive is unreachable for more than
2 seconds, fraud-scorer fails open and approves the transaction with a default score.

## Email and SMS

notification-worker uses a single email provider and a single SMS provider; outages delay receipts but do not
block payments.
