"""Run-scoped, patient-free PROV-O records for the synthetic clinical ETL."""

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from rdflib import Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import PROV, XSD


POC = Namespace("https://example.org/grounded-elt/")


def snapshot_hash(rows: list[dict[str, str]]) -> str:
    """Hash the canonical staged rows, without persisting their identifiers."""
    content = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def graph_hash(version: str, facts: list) -> str:
    content = json.dumps(
        {"version": version, "facts": [vars(fact) for fact in facts]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def code_hash() -> str:
    digest = hashlib.sha256()
    for filename in ("clinical.py", "pipeline.py"):
        digest.update(filename.encode("ascii") + b"\0")
        digest.update((Path(__file__).parent / filename).read_bytes())
    return digest.hexdigest()


def build_provenance(run_id: str, started_at: str, ended_at: str, as_of: str,
                     graph_version: str, graph_sha256: str, code_sha256: str,
                     source_hashes: dict[str, str], metrics: dict[str, list[dict]],
                     metric_fact_ids: dict[str, list[str]], quality_status: str,
                     rule_facts: list[dict[str, str]]) -> str:
    """Construct PROV-O RDF for dataset-level lineage; no row/patient identifiers."""
    base = Namespace(f"urn:grounded-elt:run:{run_id}:")
    graph = Graph()
    graph.bind("prov", PROV)
    graph.bind("poc", POC)

    def entity(name: str) -> URIRef:
        node = base[name]
        graph.add((node, RDF.type, PROV.Entity))
        return node

    def activity(name: str) -> URIRef:
        node = base[name]
        graph.add((node, RDF.type, PROV.Activity))
        graph.add((node, PROV.wasAssociatedWith, pipeline))
        return node

    pipeline = POC["clinical-etl"]
    graph.add((pipeline, RDF.type, PROV.SoftwareAgent))
    run = activity("activity/run")
    graph.add((run, PROV.startedAtTime, Literal(started_at, datatype=XSD.dateTime)))
    graph.add((run, PROV.endedAtTime, Literal(ended_at, datatype=XSD.dateTime)))
    graph.add((run, POC.asOf, Literal(as_of, datatype=XSD.dateTime)))
    graph_snapshot = entity("entity/clinical-graph")
    graph.add((graph_snapshot, POC.version, Literal(graph_version)))
    graph.add((graph_snapshot, POC.sha256, Literal(graph_sha256)))
    for rule in rule_facts:
        fact = entity(f"entity/rule/{rule['id']}")
        graph.add((fact, POC.factId, Literal(rule["id"])))
        graph.add((fact, POC.subject, Literal(rule["subject"])))
        graph.add((fact, POC.predicate, Literal(rule["predicate"])))
        graph.add((fact, POC.value, Literal(rule["object"])))
        graph.add((fact, PROV.wasDerivedFrom, graph_snapshot))
    recipe = entity("entity/clinical-python-recipe")
    graph.add((recipe, POC.sha256, Literal(code_sha256)))
    stage = activity("activity/stage")
    graph.add((stage, PROV.used, recipe))
    for table, digest in sorted(source_hashes.items()):
        source = entity(f"entity/source/{table}")
        graph.add((source, POC.dataset, Literal(table)))
        graph.add((source, POC.sha256, Literal(digest)))
        graph.add((stage, PROV.used, source))
    staged = entity("entity/raw-staging")
    graph.add((staged, PROV.wasGeneratedBy, stage))

    normalize = activity("activity/normalize")
    graph.add((normalize, PROV.wasInformedBy, stage))
    for input_entity in (staged, graph_snapshot, recipe):
        graph.add((normalize, PROV.used, input_entity))
    encounters = entity("entity/encounter-mart")
    graph.add((encounters, PROV.wasGeneratedBy, normalize))
    graph.add((encounters, PROV.wasDerivedFrom, staged))
    for metric, rows in (("los", metrics["facility_los"]),
                         ("readmission", metrics["facility_readmission"])):
        calculate = activity(f"activity/calculate-{metric}")
        graph.add((calculate, PROV.wasInformedBy, normalize))
        for input_entity in (encounters, graph_snapshot, recipe):
            graph.add((calculate, PROV.used, input_entity))
        for fact_id in metric_fact_ids[metric]:
            graph.add((calculate, PROV.used, base[f"entity/rule/{fact_id}"]))
        for position, row in enumerate(rows, 1):
            output = entity(f"entity/metric/{metric}/{position}")
            graph.add((output, PROV.wasGeneratedBy, calculate))
            graph.add((output, PROV.wasDerivedFrom, encounters))
            graph.add((output, POC.metric, Literal(metric)))
            graph.add((output, POC.asOf, Literal(as_of, datatype=XSD.dateTime)))
            graph.add((output, POC.facilityKey, Literal(row["facility_key"])))
            for name in ("discharged_count", "total_hours", "average_hours",
                         "eligible_discharges", "readmitted_discharges", "rate_pct"):
                if name in row and row[name] is not None:
                    graph.add((output, POC[name], Literal(row[name])))
    quality_activity = activity("activity/check-quality")
    graph.add((quality_activity, PROV.wasInformedBy, base["activity/calculate-readmission"]))
    for input_entity in (encounters, graph_snapshot):
        graph.add((quality_activity, PROV.used, input_entity))
    for fact_id in metric_fact_ids["quality"]:
        graph.add((quality_activity, PROV.used, base[f"entity/rule/{fact_id}"]))
    report = entity("entity/quality-report")
    graph.add((report, PROV.wasGeneratedBy, quality_activity))
    graph.add((report, POC.status, Literal(quality_status)))
    for activity_node in (stage, normalize, base["activity/calculate-los"],
                          base["activity/calculate-readmission"], quality_activity):
        graph.add((run, PROV.wasInformedBy, activity_node))
    return graph.serialize(format="turtle")


def archive_run(connection: sqlite3.Connection, *, run_id: str, started_at: str,
                ended_at: str, as_of: str, graph_version: str, graph_sha256: str,
                code_sha256: str, source_hashes: dict[str, str],
                metrics: dict[str, list[dict]],
                metric_fact_ids: dict[str, list[str]], quality_status: str,
                graph_facts: list[dict[str, str]]) -> None:
    """Commit the provenance document and historical aggregate values with the marts."""
    rule_ids = {fact_id for ids in metric_fact_ids.values() for fact_id in ids}
    rules = [fact for fact in graph_facts if fact["id"] in rule_ids]
    rdf = build_provenance(run_id, started_at, ended_at, as_of, graph_version,
                           graph_sha256, code_sha256, source_hashes, metrics, metric_fact_ids,
                           quality_status, rules)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS clinical_run_history ("
        "run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT NOT NULL, "
        "as_of TEXT NOT NULL, graph_version TEXT NOT NULL, graph_sha256 TEXT NOT NULL, "
        "code_sha256 TEXT NOT NULL, source_hashes TEXT NOT NULL, graph_snapshot TEXT, "
        "metric_fact_ids TEXT NOT NULL, "
        "aggregate_snapshot TEXT NOT NULL, quality_status TEXT NOT NULL, prov_turtle TEXT NOT NULL)"
    )
    columns = {column[1] for column in connection.execute("PRAGMA table_info(clinical_run_history)")}
    if "graph_snapshot" not in columns:
        # Earlier POC runs cannot be retroactively assigned a graph snapshot.
        connection.execute("ALTER TABLE clinical_run_history ADD COLUMN graph_snapshot TEXT")
    connection.execute(
        "INSERT INTO clinical_run_history "
        "(run_id, started_at, ended_at, as_of, graph_version, graph_sha256, code_sha256, "
        "source_hashes, graph_snapshot, metric_fact_ids, aggregate_snapshot, quality_status, prov_turtle) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, started_at, ended_at, as_of, graph_version, graph_sha256,
         code_sha256, json.dumps(source_hashes, sort_keys=True),
         json.dumps({"version": graph_version, "facts": graph_facts}, sort_keys=True),
         json.dumps(metric_fact_ids, sort_keys=True),
         json.dumps(metrics, sort_keys=True), quality_status, rdf),
    )


def export_run_provenance(db_path: Path, run_id: str, output_dir: Path | None = None) -> Path:
    """Atomically export an archived graph; rerun this if the original export failed."""
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    destination = output_dir or db_path.with_suffix(".provenance")
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            "SELECT prov_turtle FROM clinical_run_history WHERE run_id = ?", (run_id,)
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown clinical run: {run_id}")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{run_id}.ttl"
    if target.exists():
        if target.read_text(encoding="utf-8") != row[0]:
            raise FileExistsError(f"Existing provenance export differs from archived run: {target}")
        return target
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination,
                                         prefix=".prov-", suffix=".tmp", delete=False) as temporary:
            name = temporary.name
            temporary.write(row[0])
        os.replace(name, target)
    finally:
        if name is not None and os.path.exists(name):
            os.unlink(name)
    return target


def new_run_id() -> str:
    return str(uuid4())


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
