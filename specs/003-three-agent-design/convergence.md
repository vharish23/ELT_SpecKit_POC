# Convergence: Three clinical ETL design agents

The synthetic-data design workflow passed targeted and existing regression tests. A live Gemini / Vertex AI invocation produced the mapping, three validated SQL recipes, parameters and a PASS quality report. Generated SQL was parsed and compiled using `EXPLAIN` on an empty in-memory schema, **not executed**. Changes to same-facility restrictions, predicates, joins or cohort semantics fail reference validation; approved SQL is still for human review only, not a production recipe.
