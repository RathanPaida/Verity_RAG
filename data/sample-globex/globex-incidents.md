---
title: Globex Incident Summary 2024
kind: postmortem
owner: globex-sre
doc_date: 2024-09-01
---
# Globex Incident Summary 2024

The routing-engine had two SEV2 incidents in 2024, both caused by expired TLS certificates on internal load
balancers. Certificate automation was introduced in June 2024.
