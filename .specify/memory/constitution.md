# Grounded ELT POC Constitution

## Core Principles

### I. Grounded transformations
Product classification, currency conversion, clinical facility and encounter code mappings must come from versioned graph facts, never from model-generated SQL or inferred labels. Unknown or ambiguous facts fail the run.

### II. Reproducible demonstration
Use only synthetic data. The offline path must run without credentials and produce the same mart on repeated runs.

### III. Auditable delivery
Retain raw staging records and the exact graph-fact provenance of each encounter or sales mart row. Clinical measures must state the index cohort, observation cutoff, transfer exclusions and facility attribution. Tests cover reruns, bad inputs, and changes to graph facts.

Run-level RDF provenance records must be dataset/aggregate-level only; do not publish patient or encounter identifiers in RDF. Retain the archived run record alongside the committed marts, and explicitly surface export failures.

### IV. Bounded agent permissions
Custom DeepAgents domain tools may inspect graph facts and request the fixed pipeline, but never expose identifiable records or execute model-generated SQL against the warehouse. Model-level tools are not a PHI security boundary; only synthetic records may be used.

## Workflow

Maintain `specs/001-grounded-elt/spec.md`, `plan.md`, and `tasks.md` alongside implementation. Check each acceptance criterion against tests and a sample run.

## Governance

Revise this constitution before changing the POC's data, execution, or security boundaries.

**Version**: 1.1.0 | **Ratified**: 2026-09-29 | **Last Amended**: 2026-09-29
