# Convergence: 001-grounded-elt

**Result**: Converged for the credential-free POC. Live provider execution remains an operator-run check.

| Requirement | Evidence |
| --- | --- |
| FR-001 / SC-001: stage sample orders | `test_sample_and_idempotent_rerun_with_lineage`: 3 raw rows, 3 mart rows, 4,970 cents |
| FR-002 / FR-003 / SC-003: graph traversal and provenance | `test_changed_graph_fact_changes_result`: updated rate changes totals; mart includes fact IDs and graph version |
| FR-004 / SC-002: transactional, repeatable load | Rerun test and failure tests preserve previously committed rows |
| FR-005 / SC-004: two modes and bounded agent | Offline CLI output confirmed; `test_agent_builds_with_domain_tools` constructs DeepAgents without a network call |

Run `uv run python -m unittest discover -s tests -v` and `uv run python -m grounded_elt.cli --database demo.sqlite`. A live `--agent` invocation needs a supported tool-calling model, network, and provider credentials, and was not exercised during this POC validation.
