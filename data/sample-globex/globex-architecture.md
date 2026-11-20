---
title: Globex Logistics Platform Overview
kind: architecture
owner: globex-eng
doc_date: 2024-04-10
---
# Globex Logistics Platform Overview

## Compliance

Globex completed its SOC 2 Type II audit in March 2024 with no exceptions. The audit covered all production
systems.

## Topology

The routing-engine service runs active-active in europe-west1 and europe-west4 with Spanner as its database.
Monthly cloud spend is approximately 84,000 USD.
