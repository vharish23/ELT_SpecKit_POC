# Feature Specification: Grounded synthetic sales ELT

**Feature**: `001-grounded-elt` | **Created**: 2026-09-29 | **Status**: Implemented

**Input**: "Create a quick POC using Spec Kit for automating a sample ELT pipeline. Use LangGraph DeepAgents for implementation and knowledge graphs for grounding."

## User Scenarios & Testing

### User Story 1 - Reproducible ELT (Priority: P1)

An analyst loads synthetic order CSV into raw staging and a sales mart with converted USD revenue and product families.

**Independent Test**: Run the offline CLI twice and compare mart rows and staging counts.

1. **Given** the bundled example, **when** the pipeline runs, **then** three raw rows and three mart rows are persisted, totaling USD 49.70.
2. **Given** an existing database, **when** the same run repeats, **then** rows are replaced rather than duplicated.

### User Story 2 - Traceable graph grounding (Priority: P1)

An analyst can trace every classification and currency conversion to facts in the versioned knowledge graph.

**Independent Test**: Change a graph rate, rerun, and inspect mart lineage and revenue.

1. **Given** a known SKU and currency, **when** it is transformed, **then** the product-to-category-to-family path and currency rate are recorded on its mart row.
2. **Given** a missing or ambiguous graph fact, **when** a run is attempted, **then** it fails without replacing a previous successful load.

### User Story 3 - Agent-driven run (Priority: P2)

An operator can ask a DeepAgents agent to inspect graph evidence and execute the same validated pipeline.

**Independent Test**: With a configured tool-calling model, run the agent CLI and inspect the resulting database.

1. **Given** model credentials, **when** the agent runs, **then** it can inspect the knowledge graph and request the fixed ELT operation via domain tools.

## Edge Cases

- Duplicate order IDs, malformed money/quantity, empty CSV, and unknown SKUs/currencies cause explicit errors.
- Failed validation leaves previously committed mart and staging rows intact.
- Sample data is fabricated; no patient or enterprise data is used.

## Requirements

- **FR-001**: Load synthetic CSV verbatim into a SQLite staging table.
- **FR-002**: Resolve SKU classification by traversing product -> category -> family graph edges and resolve currency -> USD-rate facts.
- **FR-003**: Calculate USD cents with decimal arithmetic; include graph version and used fact IDs in each mart row.
- **FR-004**: Validate the input shape, load staging, then transform from staged records in one transaction; failed transformation rolls back both tables and reruns are idempotent.
- **FR-005**: Expose a credential-free CLI and an optional DeepAgents CLI using only graph-inspection and fixed-execution domain tools.

## Key Entities

- **Order**: Source order ID, SKU, quantity, unit price, and currency.
- **Graph fact**: Identified subject/predicate/object statement with versioned provenance.
- **Sales row**: Order, reporting family, converted USD cents, and fact IDs.

## Success Criteria

- **SC-001**: Bundled fixture produces exactly 3 staging and 3 mart rows and 4,970 USD cents.
- **SC-002**: Running twice does not change counts or totals.
- **SC-003**: Changing a rate changes computed results and lineage; unknown or ambiguous facts stop a run.
- **SC-004**: Offline tests pass without provider credentials; agent construction uses the DeepAgents API.

## Assumptions

- This is a local demonstration, not a production scheduler or enterprise graph database.
- A JSON triple graph and SQLite are adequate to demonstrate graph traversal and fact-level provenance.
- A tool-calling provider model and its credential are supplied by the operator for live agent mode.
