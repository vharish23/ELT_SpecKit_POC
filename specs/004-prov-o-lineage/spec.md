# Feature Specification: Run-level PROV-O lineage

**Feature**: `004-prov-o-lineage` | **Status**: Implemented | **Created**: 2026-09-29

## User journeys

1. An analyst runs the existing clinical ETL and receives a unique run ID and PROV-O Turtle file. The graph links source snapshots, raw staging, encounter normalization, rule graph, recipe version, LOS/readmission calculations, and facility metric snapshots using standard PROV-O activities, entities and agents.
2. An auditor inspects a past run, even after a rerun changes the current marts, and recovers its RDF, source hashes, graph hash, recipe hash, cutoff, aggregate snapshot and quality verdict from SQLite. The current run is independently identified.
3. If validation fails, neither a run record nor RDF file is produced. If export fails *after commit*, the error is surfaced and the stored document can be re-exported without recomputing clinical results.

## Requirements

- Emit patient-free RDF using actual W3C PROV-O terms `prov:Entity`, `prov:Activity`, `prov:SoftwareAgent`, `prov:used`, `prov:wasGeneratedBy`, `prov:wasDerivedFrom`, `prov:wasAssociatedWith`, and `prov:wasInformedBy`.
- Never write patient IDs, encounter IDs or individual row data into the RDF. Facility metric nodes use run-local ordinal identifiers. Historical aggregates remain in the protected SQLite database.
- Hash canonical staged source snapshots, graph facts and the clinical Python recipe with SHA-256. Retain per-run metric definition fact IDs, output aggregates, evaluation cutoff, and quality PASS/FAIL.
- Preserve the existing clinical measures, sales example, and design-agent review-only behavior. A failed quality threshold records `FAIL` without falsely implying the deterministic ETL did not run.
- Keep exports in an ignored sibling `*.provenance/` directory. Document that small-cohort aggregates and source hashes can still be sensitive and that this is not a production PHI-control system.
