# Graph-grounded ETL POC

A **synthetic-data-only** example of [Spec Kit spec-driven development](https://github.com/github/spec-kit#spec-driven-development), [LangGraph DeepAgents](https://docs.langchain.com/oss/python/deepagents/overview) orchestration and versioned knowledge-graph grounding. See the clinical feature's [specification](specs/002-clinical-quality-elt/spec.md), [plan](specs/002-clinical-quality-elt/plan.md), [tasks](specs/002-clinical-quality-elt/tasks.md), and [convergence evidence](specs/002-clinical-quality-elt/convergence.md). The original synthetic [sales POC](specs/001-grounded-elt/spec.md) remains available.

## Run the clinical example

From this directory, with Python 3.11+ and `uv`:

```sh
uv sync
uv run python -m grounded_elt.cli --pipeline clinical --database demo.sqlite
uv run python -m unittest discover -s tests -v
```

`clinical` is the default pipeline. It runs without credentials. To change the UTC observation cutoff, pass `--as-of 2026-05-01T00:00:00`. To supply another set of **synthetic** extracts, pass `--data-dir /path/to/data` and `--clinical-graph /path/to/graph.json`; expected headers are defined in `grounded_elt/clinical.py`.

### Illustrative sample results (as of 2026-05-31 UTC)

| Index facility | Discharged stays | Total LOS hours | Average LOS hours | Eligible 30-day discharges | Unplanned readmissions | Rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| North Hospital | 7 | 312.00 | 44.57 | 6 | 3 | 50.00% |
| South Hospital | 2 | 96.00 | 48.00 | 2 | 0 | 0.00% |

The four *illustrative*, locally defined inputs are `data/clinical/clarity_pat_enc_hsp.csv` (inpatient timestamps, type, source and disposition), `caboodle_encounter_fact.csv` (encounter-to-patient/facility keys), `caboodle_patient_dim.csv` (patient key bridge), and `caboodle_facility_dim.csv` (facility label). These CSVs stage into corresponding `raw_*` tables. The subsequent pipelines materialize `encounter_mart`, `facility_los`, `readmission_index`, and `facility_readmission` in one transaction. Repeated runs replace the snapshot; malformed joins, timestamp ranges, overlaps or graph facts roll back all changed data.

`data/knowledge_graph.json` is the **canonical clinical** subject-predicate-object graph (`--clinical-graph` can override it). Each encounter traverses facility key -> canonical site -> name plus type/source/disposition code -> reporting concept. The `metric:los` facts identify and validate the Clarity admission/discharge fields, Caboodle facility field and elapsed-hours unit. The `metric:readmission` facts define the window in days, eligible index disposition, qualifying return type, excluded return source and index-facility attribution. The pipeline reads and validates these facts rather than hard-coding the readmission window and cohort codes; unsupported LOS fields or attribution fail explicitly instead of pretending they were used. The encounter mart retains source encounter/patient keys, graph version and the five graph fact IDs used to derive each row. `metric_definition` stores the measure-fact IDs and graph version used for the **current** snapshot; historical rule IDs are in `clinical_run_history`. Metric outputs retain graph version and cutoff, and `readmission_index` identifies eligibility and the first qualifying return encounter for audit.

### PROV-O run lineage

Each successful clinical run returns `run_id`, `quality_status`, and `provenance_path` in its JSON output. For `--database demo.sqlite`, the Turtle RDF export is in `demo.provenance/<run_id>.ttl` (git-ignored). The [PROV-O feature spec](specs/004-prov-o-lineage/spec.md) describes the boundary: **source snapshot -> staging -> normalized encounters -> LOS/readmission aggregate**, with rule-graph and clinical-recipe inputs and a quality-check activity. `prov:used`, `prov:wasGeneratedBy`, `prov:wasDerivedFrom`, `prov:wasAssociatedWith` and `prov:wasInformedBy` connect run-scoped RDF entities, activities and the pipeline agent. This works in both direct and `--agent` clinical modes; `--design-agents` still never executes generated SQL.

`clinical_run_history` archives one append-only-in-the-application record per run: exact exported RDF, canonical staged-source SHA-256 digests (not source rows), the graph facts snapshot and its SHA-256, combined clinical/graph-loader Python recipe SHA-256, cutoff, graph version, metric rule IDs, aggregate results and PASS/FAIL quality verdict. `clinical_current_run` points to the currently materialized marts. Rerunning replaces the marts but does **not** discard historical run records or prior RDF files. A failed validation does not create a run. A failure to write the RDF **after the SQLite commit** is surfaced; restore the export from the archived RDF without recomputing metrics:

```sh
uv run python -m grounded_elt.cli --database demo.sqlite --export-run RUN_ID
sqlite3 demo.sqlite 'SELECT run_id, graph_version, as_of, quality_status FROM clinical_run_history;'
sqlite3 demo.sqlite 'SELECT run_id FROM clinical_current_run;'
```

No patient or encounter IDs are placed in the RDF; metric nodes use per-run ordinal identifiers and carry facility keys and aggregate values. The SQLite warehouse *does* hold synthetic row-level data. Hashes of small/guessable datasets and low-count aggregate outputs may still reveal information, so this is **not** a public-data or PHI-safe export policy for production. The current quality verdict reflects deterministic graph thresholds; a `FAIL` still records a successfully computed mart, while the separate three-agent design workflow gates its own output on quality. The hashes alone do not preserve source files for replay; source extracts still need separate approved retention.

### Measure definitions

- **LOS**: elapsed discharge minus admission, including transferred discharges, aggregated by the encounter's facility; sample averages are rounded to two decimal hours.
- **30-day readmission** (current graph version): denominator is inpatient encounters with `home` discharge and a fully observed 720-hour post-discharge window ending no later than `--as-of`. Numerator counts each eligible index discharge **once** if a later inpatient encounter for the same patient at *either* facility starts strictly after discharge and at most 720 hours later, maps to `unplanned`, and does **not** have a `transfer` admission source. Planned returns do not count; transfer and expired index discharges and immature follow-up are excluded. An encounter may be a return for one index and an index for a later return. The rate is attributed to the **index** facility; zero denominator yields SQL `NULL`.
- These are demonstrator definitions, **not** a CMS/HEDIS quality measure: no risk adjustment, diagnosis exclusions, outside-network encounters, or full planned-readmission algorithm.

Example query (when the SQLite CLI is installed):

```sh
sqlite3 -header -column demo.sqlite 'SELECT facility_name, discharged_count, average_hours FROM facility_los;'
sqlite3 -header -column demo.sqlite 'SELECT facility_name, eligible_discharges, readmitted_discharges, rate_pct FROM facility_readmission;'
sqlite3 -header -column demo.sqlite 'SELECT encounter_key, eligibility, readmission_encounter_key FROM readmission_index ORDER BY encounter_key;'
sqlite3 -header -column demo.sqlite 'SELECT metric, graph_version, fact_ids FROM metric_definition;'
```

### Optional DeepAgents orchestration

With a Gemini Developer API key, set it **in your own shell** (do not paste it into chat or commit it) and explicitly select a tool-calling Gemini model:

```sh
export GOOGLE_API_KEY="your-key"  # Gemini Developer API; do not commit this value
uv run python -m grounded_elt.cli --pipeline clinical --agent --model google_genai:gemini-2.5-flash
```

The Google integration also accepts `GEMINI_API_KEY` as a fallback. If using an OpenAI key instead:

```sh
export OPENAI_API_KEY="your-key"  # never commit credentials
uv run python -m grounded_elt.cli --pipeline clinical --agent --model openai:gpt-4.1-mini
```

The custom tools expose graph mappings and a fixed, validated ETL operation; no patient records are passed by these tools to the model. DeepAgents also provides its own built-in agent tooling, so this demonstration is **not a PHI security boundary**. Do not use identifiable records or an external model without site-specific privacy, security, data licensing and Epic schema reviews. Offline tests require no model calls. A live Gemini/Vertex AI run using ADC has succeeded with this synthetic fixture; other models and real-data deployments are unverified.

### Three-agent design workflow (review only)

The [three-agent feature](specs/003-three-agent-design/spec.md) implements separately invoked DeepAgents roles:

1. **Source Mapping** reads only schema and clinical graph metadata and produces `mapping.json`.
2. **Recipe Generation** produces encounter, LOS and readmission SELECT recipes. These are checked against graph-grounded reference SQL, parsed by sqlglot and **compiled only** by SQLite `EXPLAIN` against an empty in-memory schema. They are never executed, and a changed cross-facility or cohort predicate fails validation.
3. **Sufficiency & Quality** uses only synthetic aggregate evidence (validated by running the *existing deterministic* clinical pipeline in a temporary warehouse) and the graph's `quality:clinical` thresholds. It reports per-rule results; a failure exits nonzero.

For an ADC-authenticated Google Cloud account with access to Gemini on Vertex AI:

```sh
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_CLOUD_LOCATION=us-central1
uv run python -m grounded_elt.cli --pipeline clinical --design-agents \
  --model google_genai:gemini-2.5-flash --output-dir design-output
```

`design-output/` (ignored by git) contains `mapping.json`, three `*_sql.sql` drafts, `recipe_parameters.json`, and `quality.json`. Supply the named parameters from that JSON **only after human review in a separately approved environment**. The default graph thresholds require 100% code-mapping and required-field coverage, zero orphan joins, and at least two eligible discharges per facility. These are **POC thresholds**, not statistically meaningful clinical performance benchmarks. `--design-agents` is separate from `--agent` (which executes the fixed ETL); the design workflow never changes `demo.sqlite` or runs its draft SQL.

### Existing sales example

```sh
uv run python -m grounded_elt.cli --pipeline sales --database demo.sqlite
```

It stages `data/orders.csv` to `raw_orders`, traverses the separate `data/sales_knowledge_graph.json` to derive product family and currency rate, and loads `sales_mart` with graph fact lineage (3 rows, USD 49.70). The two examples have separate raw and mart tables and may share a SQLite file.

**Important**: Epic Clarity/Caboodle schemas and access vary by installation and license. These files are invented sample *Clarity/Caboodle-like* tables, **not official Epic sample exports or production-compatible schemas**. Map the actual local data dictionary, approved code sets, privacy constraints and measurement policy before connecting to real data.
