# Implementation Plan: Grounded synthetic sales ELT

**Feature**: `001-grounded-elt` | **Date**: 2026-09-29 | **Spec**: [spec.md](./spec.md)

## Summary

Use Spec Kit's constitution -> specification -> plan -> tasks -> implementation -> convergence lifecycle. Implement a deterministic local CSV -> SQLite staging -> graph-grounded mart pipeline. Add a DeepAgents orchestration entry point with read-only graph inspection and a fixed ELT tool. No model-generated SQL is executed.

## Technical Context

**Language/Version**: Python >=3.11
**Primary Dependencies**: DeepAgents, LangChain OpenAI integration for the optional model
**Storage**: SQLite warehouse; versioned JSON subject-predicate-object knowledge graph
**Testing**: Python standard-library unittest
**Target Platform**: Local CLI
**Constraints**: Synthetic data only; no network or credentials required for offline mode; fail atomically on bad data

## Constitution Check

- Graph is the authoritative source for conversion and classification: satisfied by graph traversal in the transformer.
- Agent permissions bounded: only domain-specific functions supplied; no arbitrary SQL tool.
- Reproducibility and auditability: raw staging, lineage, repeatability and failure tests.

## Design

1. Read and validate CSV shape and graph structure; begin a SQLite transaction and load raw records into staging.
2. Read staged records back, traverse product -> category -> family and currency -> USD rate, compute cents with Decimal, preserve each fact ID as lineage.
3. Replace staging and mart in one transaction; roll back both if a staged row cannot be transformed.
4. Expose `--agent` mode through `create_deep_agent(model=..., tools=..., system_prompt=...)`; use the same verified loader in offline and agent modes.

## Project Structure

```text
data/orders.csv
data/sales_knowledge_graph.json
grounded_elt/__init__.py
grounded_elt/pipeline.py
grounded_elt/cli.py
tests/test_pipeline.py
specs/001-grounded-elt/{spec,plan,tasks}.md
```

**Scope boundary**: This is not a scheduler, distributed ELT engine, governance service, or clinical data pipeline.
