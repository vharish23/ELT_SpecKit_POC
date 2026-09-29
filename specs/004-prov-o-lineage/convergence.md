# Convergence: Run-level PROV-O lineage

**Result**: Converged for synthetic clinical ETL.

- Every successful run persists an ID, PROV-O Turtle, source snapshot hashes, graph facts and hash, clinical/loader code hash, cutoff, archived facility aggregates and PASS/FAIL quality verdict in `clinical_run_history`. `clinical_current_run` identifies the active marts.
- RDF parse tests assert standard PROV-O activity/entity relationships from source staging to encounter normalization, rule use, facility metrics and quality report. RDF contains aggregate-only metrics and no encounter or patient identifiers.
- Failed validation rolls back current marts and run archive. Post-commit export errors surface and can be recovered with `--export-run`. Existing history schemas are upgraded without inventing missing graph snapshots for older runs.
- Full `unittest` suite: 37 passing. The clinical CLI generated an RDF file and retained its archived readmission count of 3 for North Hospital; re-export from the SQLite archive succeeded. No model call is required for provenance; the existing agent mode shares the same clinical runner.

This is not a public-data or PHI export policy. Source hashes do not retain the source rows, and aggregates from small cohorts require disclosure review.
