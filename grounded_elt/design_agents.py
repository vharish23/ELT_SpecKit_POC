"""Three DeepAgents roles producing review-only ETL design artifacts."""

import json
import sqlite3
import tempfile
from pathlib import Path

from sqlglot import exp, parse

from grounded_elt.clinical import (
    QUALITY_PREDICATES, SOURCES, _metric_policy, _read_source, quality_limits, run_clinical_pipeline,
)
from grounded_elt.pipeline import KnowledgeGraph


MAPPINGS = {
    "encounter_key": "caboodle:EncounterFact.EncounterKey",
    "clarity_csn": "caboodle:EncounterFact.ClarityEncounterId -> clarity:PAT_ENC_HSP.PAT_ENC_CSN_ID",
    "patient_id": "caboodle:EncounterFact.PatientDurableKey -> caboodle:PatientDim.ClarityPatId (equals clarity:PAT_ENC_HSP.PAT_ID)",
    "facility_key": "caboodle:EncounterFact.FacilityKey",
    "facility_name": "caboodle:FacilityDim.FacilityName (via FacilityKey; checked against graph site display_name)",
    "admit_at": "clarity:PAT_ENC_HSP.HOSP_ADMSN_TIME",
    "discharge_at": "clarity:PAT_ENC_HSP.HOSP_DISCH_TIME",
    "los_seconds": "discharge_at - admit_at (UTC elapsed seconds)",
    "admit_kind": "clarity:PAT_ENC_HSP.ADMIT_TYPE_C -> graph admit_type maps_to",
    "admit_source": "clarity:PAT_ENC_HSP.ADMIT_SOURCE_C -> graph admit_source maps_to",
    "disposition": "clarity:PAT_ENC_HSP.DISCH_DISPOSITION_C -> graph disposition maps_to",
}
RECIPE_KEYS = ("encounter_sql", "los_sql", "readmission_sql")
ALLOWED_TABLES = set(SOURCES) | {"encounter_mart"}


def mapping_evidence(graph: KnowledgeGraph) -> dict:
    _metric_policy(graph)
    return {
        "graph_version": graph.version,
        "source_schemas": {table: list(columns) for table, (_, columns, _) in SOURCES.items()},
        "target_mappings": MAPPINGS,
        "graph_facts": [vars(fact) for fact in graph.facts if fact.subject.startswith(("metric:", "facility:", "site:", "admit_", "disposition:"))],
    }


def quality_evidence(data_dir: Path, graph_path: Path, as_of: str) -> dict:
    graph = KnowledgeGraph(graph_path)
    _metric_policy(graph)
    limits = quality_limits(graph)
    sources = {
        table: _read_source(data_dir / filename, columns, key)
        for table, (filename, columns, key) in SOURCES.items()
    }
    total_cells = sum(len(rows) * len(SOURCES[table][1]) for table, rows in sources.items())
    missing_cells = sum(
        not value.strip()
        for rows in sources.values() for row in rows for value in row.values()
    )
    with tempfile.TemporaryDirectory() as directory:
        summary = run_clinical_pipeline(data_dir, graph_path, Path(directory) / "quality.sqlite", as_of)
    # The clinical loader rejects unmapped codes and orphan joins before returning a summary.
    evidence = {
        "graph_version": graph.version,
        "mapping_coverage_pct": 100,
        "missing_required_pct": 100 * missing_cells / total_cells,
        "orphan_joins": 0,
        "eligible_by_facility": {
            row["facility_name"]: row["eligible_discharges"]
            for row in summary["facility_readmission"]
        },
        "source_row_counts": {table: len(rows) for table, rows in sources.items()},
        "thresholds": limits,
        "threshold_fact_ids": [graph.edge("quality:clinical", name).id for name in QUALITY_PREDICATES],
    }
    return evidence


def quality_decision(evidence: dict) -> dict:
    limits = evidence["thresholds"]
    checks = {
        "mapping_coverage": evidence["mapping_coverage_pct"] >= limits["min_mapping_coverage_pct"],
        "missing_required": evidence["missing_required_pct"] <= limits["max_missing_required_pct"],
        "orphan_joins": evidence["orphan_joins"] <= limits["max_orphan_joins"],
        "eligible_per_facility": all(
            count >= limits["min_eligible_per_facility"]
            for count in evidence["eligible_by_facility"].values()
        ) and bool(evidence["eligible_by_facility"]),
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "evidence": evidence}


def reference_recipes(graph: KnowledgeGraph) -> dict[str, str]:
    """Approved, graph-grounded SELECT shapes; dynamic model SQL must match them."""
    _metric_policy(graph)

    def mapped_codes(prefix: str, column: str) -> str:
        facts = sorted((fact for fact in graph.facts
                        if fact.subject.startswith(prefix) and fact.predicate == "maps_to"),
                       key=lambda fact: fact.subject)
        if not facts:
            raise ValueError(f"Missing graph mappings for {prefix}")
        def literal(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        cases = " ".join(
            f"WHEN {literal(fact.subject.removeprefix(prefix))} THEN {literal(fact.object)}"
            for fact in facts
        )
        return f"CASE {column} {cases} END"

    return {
        "encounter_sql": (
            "SELECT e.EncounterKey AS encounter_key, h.PAT_ENC_CSN_ID AS clarity_csn, "
            "h.PAT_ID AS patient_id, e.FacilityKey AS facility_key, f.FacilityName AS facility_name, "
            "h.HOSP_ADMSN_TIME AS admit_at, h.HOSP_DISCH_TIME AS discharge_at, "
            "CAST(strftime('%s', h.HOSP_DISCH_TIME) AS INTEGER) - "
            "CAST(strftime('%s', h.HOSP_ADMSN_TIME) AS INTEGER) AS los_seconds, "
            f"{mapped_codes('admit_type:', 'h.ADMIT_TYPE_C')} AS admit_kind, "
            f"{mapped_codes('admit_source:', 'h.ADMIT_SOURCE_C')} AS admit_source, "
            f"{mapped_codes('disposition:', 'h.DISCH_DISPOSITION_C')} AS disposition "
            "FROM raw_caboodle_encounter_fact e "
            "JOIN raw_clarity_pat_enc_hsp h ON e.ClarityEncounterId = h.PAT_ENC_CSN_ID "
            "JOIN raw_caboodle_patient_dim p ON e.PatientDurableKey = p.PatientDurableKey "
            "AND p.ClarityPatId = h.PAT_ID "
            "JOIN raw_caboodle_facility_dim f ON e.FacilityKey = f.FacilityKey"
        ),
        "los_sql": (
            "SELECT facility_key, facility_name, COUNT(*) AS discharged_count, "
            "ROUND(SUM(los_seconds) / 3600.0, 2) AS total_hours, "
            "ROUND(AVG(los_seconds) / 3600.0, 2) AS average_hours "
            "FROM encounter_mart WHERE datetime(discharge_at) <= datetime(:as_of) "
            "GROUP BY facility_key, facility_name"
        ),
        "readmission_sql": (
            "SELECT i.facility_key, i.facility_name, COUNT(*) AS eligible_discharges, "
            "SUM(CASE WHEN EXISTS (SELECT 1 FROM encounter_mart r "
            "WHERE r.patient_id = i.patient_id AND datetime(r.admit_at) > datetime(i.discharge_at) "
            "AND datetime(r.admit_at) <= datetime(i.discharge_at, '+' || :window_days || ' days') "
            "AND r.admit_kind = :return_kind AND r.admit_source <> :exclude_source) "
            "THEN 1 ELSE 0 END) AS readmitted_discharges "
            "FROM encounter_mart i WHERE i.disposition = :index_disposition "
            "AND datetime(i.discharge_at, '+' || :window_days || ' days') <= datetime(:as_of) "
            "GROUP BY i.facility_key, i.facility_name"
        ),
    }


def _last_json(response: dict) -> dict:
    content = response["messages"][-1].content
    if isinstance(content, list):
        blocks = [block["text"] for block in content
                  if isinstance(block, dict) and block.get("type") == "text"
                  and isinstance(block.get("text"), str)]
        if len(blocks) != 1:
            raise ValueError("Agent must return exactly one JSON text block")
        content = blocks[0]
    if not isinstance(content, str):
        raise ValueError("Agent must return a JSON object as text")
    content = content.strip()
    if content.startswith("```json\n") and content.endswith("\n```"):
        content = content[len("```json\n"):-len("\n```")]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("Agent must return a JSON object")
    return parsed


def validate_recipes(recipes: dict, reference: dict[str, str]) -> None:
    if set(recipes) != set(RECIPE_KEYS):
        raise ValueError(f"Expected SQL recipes: {RECIPE_KEYS}")
    for name, sql in recipes.items():
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError(f"Missing SQL for {name}")
        expressions = parse(sql, read="sqlite")
        if len(expressions) != 1 or not isinstance(expressions[0], exp.Select):
            raise ValueError(f"{name} must be one SELECT statement")
        expression = expressions[0]
        if any(isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Command))
               for node in expression.walk()):
            raise ValueError(f"{name} contains a non-SELECT operation")
        tables = {table.name for table in expression.find_all(exp.Table)}
        if not tables or not tables <= ALLOWED_TABLES:
            raise ValueError(f"{name} uses unsupported tables: {tables - ALLOWED_TABLES}")
        if name == "encounter_sql" and tables != set(SOURCES):
            raise ValueError("encounter_sql must join all four source tables")
        if name in {"los_sql", "readmission_sql"} and tables != {"encounter_mart"}:
            raise ValueError(f"{name} must query the normalized encounter mart")
        expected = parse(reference[name], read="sqlite")
        if expression != expected[0]:
            raise ValueError(f"{name} deviates from the graph-grounded reference semantics")
        # Compile against the actual local staging/encounter schema, without running generated SQL.
        with sqlite3.connect(":memory:") as connection:
            for table, (_, columns, _) in SOURCES.items():
                connection.execute(
                    f'CREATE TABLE "{table}" ({", ".join(f"{column} TEXT" for column in columns)})'
                )
            connection.execute(
                "CREATE TABLE encounter_mart (encounter_key TEXT, clarity_csn TEXT, patient_id TEXT, "
                "facility_key TEXT, facility_name TEXT, admit_at TEXT, discharge_at TEXT, los_seconds INTEGER, "
                "admit_kind TEXT, admit_source TEXT, disposition TEXT, graph_version TEXT, fact_ids TEXT)"
            )
            try:
                connection.execute("EXPLAIN " + sql, {
                    "as_of": "2026-05-31T00:00:00", "window_days": 30,
                    "index_disposition": "home", "return_kind": "unplanned",
                    "exclude_source": "transfer",
                }).fetchall()
            except sqlite3.Error as exc:
                raise ValueError(f"{name} does not compile against sample schema: {exc}") from exc


def run_design_agents(data_dir: Path, graph_path: Path, as_of: str, model: str,
                      agent_factory=None) -> dict:
    """Invoke three specialized agents in order; generated SQL is never executed."""
    if agent_factory is None:
        from deepagents import create_deep_agent
        agent_factory = create_deep_agent
    graph = KnowledgeGraph(graph_path)
    mapping = mapping_evidence(graph)
    policy = _metric_policy(graph)
    reference = reference_recipes(graph)
    quality = quality_decision(quality_evidence(data_dir, graph_path, as_of))

    def inspect_mapping_contract() -> str:
        """Return grounded clinical source schemas, target fields, and versioned graph evidence."""
        return json.dumps(mapping)

    mapper = agent_factory(
        model=model, tools=[inspect_mapping_contract],
        system_prompt=(
            "You are the Source Mapping agent. Call inspect_mapping_contract. Return ONLY a JSON object "
            "with graph_version and target_mappings exactly as supplied by the tool. "
            "Do not invent or alter columns, paths or facts."
        ),
    )
    mapped = _last_json(mapper.invoke({"messages": [{"role": "user", "content": "Produce the source-to-target mapping."}]}))
    if mapped != {"graph_version": graph.version, "target_mappings": MAPPINGS}:
        raise ValueError("Mapping agent output differs from grounded contract")

    def inspect_recipe_inputs() -> str:
        """Return verified mapping, graph facts, and reviewed SELECT recipes for SQL generation."""
        return json.dumps({"mapping": mapped, "source_schemas": mapping["source_schemas"],
                           "measure_facts": [fact for fact in mapping["graph_facts"] if fact["subject"].startswith("metric:")],
                           "reference_recipes": reference})

    recipe_agent = agent_factory(
        model=model, tools=[inspect_recipe_inputs],
        system_prompt=(
            "You are the SQL Recipe Generation agent. Call inspect_recipe_inputs. Return ONLY JSON with "
            "encounter_sql, los_sql, readmission_sql: one SQLite SELECT per key, no DDL/DML, no code fences. "
            "Return the SQL from reference_recipes EXACTLY as given by the tool, without changing predicates, "
            "joins, aggregation, parameters or date comparisons. These are REVIEW-ONLY SQL recipes, "
            "not executable production ETL."
        ),
    )
    recipe_messages = [{"role": "user", "content": "Draft three grounded SELECT-only recipes."}]
    for attempt in range(3):
        recipes = _last_json(recipe_agent.invoke({"messages": recipe_messages}))
        try:
            validate_recipes(recipes, reference)
            break
        except ValueError as exc:
            if attempt == 2:
                raise
            recipe_messages.append({
                "role": "user",
                "content": f"Your draft failed validation: {exc}. Copy reference_recipes exactly; return only JSON.",
            })

    def inspect_quality_evidence() -> str:
        """Return aggregate-only coverage, orphan, completeness, volume evidence and graph thresholds."""
        return json.dumps(quality)

    quality_agent = agent_factory(
        model=model, tools=[inspect_quality_evidence],
        system_prompt=(
            "You are the Sufficiency & Quality agent. Call inspect_quality_evidence. "
            "Return ONLY a JSON object with status (PASS or FAIL) and checks copied exactly "
            "from the evidence tool. Do not invent thresholds or see patient-level records."
        ),
    )
    decision = _last_json(quality_agent.invoke({"messages": [{"role": "user", "content": "Assess sufficiency and quality."}]}))
    if decision != {"status": quality["status"], "checks": quality["checks"]}:
        raise ValueError("Quality agent output differs from measured evidence")
    return {"mapping": mapped, "recipes": recipes, "recipe_parameters": {
        "as_of": as_of,
        "window_days": policy.window.days,
        "index_disposition": policy.index_disposition,
        "return_kind": policy.return_admit_kind,
        "exclude_source": policy.excluded_return_source,
    }, "quality": quality}


def save_design(result: dict, output_dir: Path) -> None:
    """Write review artifacts after all three agents have completed successfully."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("mapping", "quality", "recipe_parameters"):
        (output_dir / f"{name}.json").write_text(json.dumps(result[name], indent=2) + "\n", encoding="utf-8")
    for name, sql in result["recipes"].items():
        (output_dir / f"{name}.sql").write_text(sql.rstrip() + "\n", encoding="utf-8")
