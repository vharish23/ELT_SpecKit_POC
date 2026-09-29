# Feature Specification: Three clinical ETL design agents

**Feature**: `003-three-agent-design` | **Status**: Implemented | **Created**: 2026-09-29

## User stories

1. **Source Mapping agent**: Given the four synthetic Clarity/Caboodle-like schemas and the clinical knowledge graph, provide an explicit source-to-target encounter mapping with the graph version. Refuse mappings that disagree with the grounded contract.
2. **Recipe Generation agent**: Produce three human-reviewable SQLite SELECT recipes for encounter normalization, facility LOS, and 30-day readmissions. Compile and check each against graph-grounded reference semantics; **never execute generated SQL**.
3. **Sufficiency & Quality agent**: Inspect aggregate-only data evidence, use graph-backed thresholds, and report PASS/FAIL with per-rule results. A failing gate must exit nonzero without running SQL or replacing the warehouse.

## Acceptance criteria

- Agents run separately in order, receive only synthetic schema/graph facts or aggregate metrics, and produce `mapping.json`, `encounter_sql.sql`, `los_sql.sql`, `readmission_sql.sql`, `recipe_parameters.json`, and `quality.json`.
- Quality thresholds: mapping coverage >= 100%, required-field missing <= 0%, orphan joins <= 0, at least two eligible index discharges per sample facility. Missing or invalid thresholds fail explicitly.
- Quality preflight uses the existing deterministic pipeline in a *temporary* SQLite database; review recipes are not executed. SQL that changes cross-facility semantics or 30-day logic fails validation even when it parses.
- Existing `--agent`, offline clinical, and sales modes remain functional. No real patient data, production Epic schema, or production SQL deployment.
