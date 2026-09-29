# Implementation Plan: Three clinical ETL design agents

**Feature**: `003-three-agent-design` | **Spec**: [spec.md](./spec.md)

Use three sequential `create_deep_agent` invocations, each with a narrow read-only tool, in `grounded_elt/design_agents.py`. Source mapping uses schema and canonical graph metadata; recipe agent uses mapping evidence plus graph-grounded reference SELECT templates; quality agent receives only aggregate validation results and thresholds. The orchestrator rejects model outputs that disagree with the verified mapping, SQL AST/reference recipe, or computed quality verdict. Parse and compile SQL via sqlglot and SQLite `EXPLAIN` against an in-memory *schema only*; do not run model SQL. Save review artifacts under an ignored folder after validation, and fail the CLI quality gate when thresholds fail.

Add `quality:clinical` threshold facts to the canonical graph. Preserve the deterministic ETL as the only computation of patient-level measures (in a temporary database during preflight). Test response parsing, semantic rejection, changed thresholds, missing graph facts, and all existing modes.
