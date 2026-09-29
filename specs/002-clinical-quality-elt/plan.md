# Implementation Plan: Synthetic clinical quality ETL

**Feature**: `002-clinical-quality-elt` | **Spec**: [spec.md](./spec.md)

## Technical plan

- Reuse the existing `KnowledgeGraph` triple validator from `grounded_elt/pipeline.py`. Keep sales facts under `data/sales_knowledge_graph.json` and make `data/knowledge_graph.json` the canonical clinical graph with code mappings, validated LOS source metadata and executable readmission cohort/window facts.
- Implement `grounded_elt/clinical.py`: validated staged source tables, patient/encounter/facility reconciliation, graph-grounded encounter mart, facility LOS aggregate, 30-day across-facility readmission aggregate. Execute stages sequentially in a single SQLite transaction.
- `grounded_elt/cli.py` selects `--pipeline clinical` by default; preserve `--pipeline sales`, and supply clinical-specific DeepAgents tools in agent mode. Do not send source records to a model; graph tool only exposes code mappings. Model invocation remains opt-in.
- Unit and integration tests target fixture totals, attribution, cohort boundary and numerator edge cases, missing/invalid measure facts, graph-driven policy changes, rollback, repeatability, agent construction and sales regression.

## Governance

The existing constitution's synthetic-data, graph-authoritative and bounded-tool requirements remain in force. No production Epic adapter is provided.
