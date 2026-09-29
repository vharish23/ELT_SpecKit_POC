# Implementation Plan: Run-level PROV-O lineage

**Feature**: `004-prov-o-lineage` | **Spec**: [spec.md](./spec.md)

Use `rdflib` for standards-compliant Turtle. Generate one UUID per successful clinical run. Compute hashes from validated, canonical staged records, versioned graph facts and the Python recipe. During the *same SQLite transaction* as current marts, append the run, its generated RDF and retained aggregate snapshot to `clinical_run_history`; update `clinical_current_run`. After commit, atomically export the archived RDF to `demo.provenance/<run_id>.ttl`. An export failure raises and can be retried through `--export-run`; no run is fabricated on validation failure. The agent mode uses the same clinical runner. Tests parse RDF with rdflib, inspect paths and absence of identifiers, verify snapshot hashes/immutability and current pointer, and test error recovery.

The graph documents dataset-level and aggregate lineage only, not a patient-level graph. No generated SQL is executed. The JSON facts in `data/knowledge_graph.json` stay the rule source; the PROV-O graph is a *run artifact*, not a replacement for those facts.
