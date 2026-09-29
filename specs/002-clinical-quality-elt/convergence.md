# Convergence: 002-clinical-quality-elt

**Status**: Converged for synthetic offline demonstration. Live provider execution is unverified.

| Acceptance condition | Evidence |
| --- | --- |
| Four source extracts join to a grounded encounter mart | 9 encounters with source keys, graph version and five fact IDs; integration test checks raw tables and lineage |
| Facility LOS | North: 7 stays / 312.00 hours / 44.57 mean; South: 2 / 96.00 / 48.00 |
| 30-day across-facility unplanned readmission | North: 3 of 6, 50.00%; South: 0 of 2, 0.00%; index-level assertions check excluded transfer, planned return, cross-facility return; changing admission source to transfer removes numerator credit |
| Observation window and graph semantics | 720-hour boundary, immature window and changed mapping tests |
| Atomicity and repeatability | Rerun equality, bad join rollback, unknown/ambiguous graph rollback, overlap rejection tests |
| Preserve sales POC | Existing sales tests and `--pipeline sales` pass |
| Bounded agent entry point | Agent construction test without credentials or network; no live model call claimed |
| Canonical clinical graph and measure provenance | `data/knowledge_graph.json` defines clinical mappings and measure facts; `metric_definition` stores fact IDs; window-change and invalid-fact tests show policy is executed and validated |

Command: `uv run python -m unittest discover -s tests -v`.
