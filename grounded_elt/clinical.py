"""Illustrative Clarity/Caboodle-like extracts -> grounded quality marts."""

import csv
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from grounded_elt.pipeline import KnowledgeGraph
from grounded_elt.provenance import (
    archive_run, code_hash, export_run_provenance, graph_hash, new_run_id, now_utc, snapshot_hash,
)


SOURCES = {
    "raw_clarity_pat_enc_hsp": (
        "clarity_pat_enc_hsp.csv",
        ("PAT_ENC_CSN_ID", "PAT_ID", "HOSP_ADMSN_TIME", "HOSP_DISCH_TIME",
         "ADMIT_TYPE_C", "ADMIT_SOURCE_C", "DISCH_DISPOSITION_C"),
        "PAT_ENC_CSN_ID",
    ),
    "raw_caboodle_encounter_fact": (
        "caboodle_encounter_fact.csv",
        ("EncounterKey", "ClarityEncounterId", "FacilityKey", "PatientDurableKey"),
        "EncounterKey",
    ),
    "raw_caboodle_patient_dim": (
        "caboodle_patient_dim.csv",
        ("PatientDurableKey", "ClarityPatId"),
        "PatientDurableKey",
    ),
    "raw_caboodle_facility_dim": (
        "caboodle_facility_dim.csv",
        ("FacilityKey", "FacilityName"),
        "FacilityKey",
    ),
}
QUALITY_PREDICATES = (
    "min_mapping_coverage_pct", "max_missing_required_pct",
    "max_orphan_joins", "min_eligible_per_facility",
)


def quality_limits(graph: KnowledgeGraph) -> dict[str, int]:
    limits = {}
    for predicate in QUALITY_PREDICATES:
        value = graph.edge("quality:clinical", predicate).object
        if not value.isascii() or not value.isdecimal() or not 0 <= int(value) <= 10000:
            raise ValueError(f"Invalid quality threshold {predicate}: {value}")
        limits[predicate] = int(value)
    if limits["min_mapping_coverage_pct"] > 100 or limits["max_missing_required_pct"] > 100:
        raise ValueError("Invalid quality percentage threshold")
    return limits


@dataclass(frozen=True)
class ClinicalPolicy:
    window: timedelta
    index_disposition: str
    return_admit_kind: str
    excluded_return_source: str
    fact_ids: tuple[str, ...]


def _metric_policy(graph: KnowledgeGraph) -> ClinicalPolicy:
    los_fields = {
        "admit_field": "clarity:PAT_ENC_HSP.HOSP_ADMSN_TIME",
        "discharge_field": "clarity:PAT_ENC_HSP.HOSP_DISCH_TIME",
        "facility_field": "caboodle:EncounterFact.FacilityKey",
        "reporting_unit": "elapsed_hours",
    }
    los_facts = [graph.edge("metric:los", predicate) for predicate in los_fields]
    for fact in los_facts:
        if fact.object != los_fields[fact.predicate]:
            raise ValueError(f"Unsupported LOS definition: {fact.predicate}={fact.object}")
    predicates = (
        "window_days", "index_disposition", "return_admit_kind",
        "excluded_return_source", "attribution",
    )
    readmission_facts = [graph.edge("metric:readmission", predicate) for predicate in predicates]
    window_fact, index_fact, return_fact, exclude_fact, attribution_fact = readmission_facts
    if not window_fact.object.isascii() or not window_fact.object.isdecimal() or not 1 <= int(window_fact.object) <= 365:
        raise ValueError(f"Invalid readmission window_days: {window_fact.object}")
    if (index_fact.object not in {"home", "transfer", "expired"}
            or return_fact.object not in {"planned", "unplanned"}
            or exclude_fact.object not in {"community", "transfer"}
            or attribution_fact.object != "index_facility"):
        raise ValueError("Unsupported readmission policy concept or attribution")
    return ClinicalPolicy(
        timedelta(days=int(window_fact.object)),
        index_fact.object, return_fact.object, exclude_fact.object,
        tuple(fact.id for fact in readmission_facts),
    )


def _record_metric_facts(connection: sqlite3.Connection, graph: KnowledgeGraph, as_of: datetime,
                         policy: ClinicalPolicy) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS metric_definition ("
        "metric TEXT PRIMARY KEY, graph_version TEXT NOT NULL, fact_ids TEXT NOT NULL, as_of TEXT NOT NULL)"
    )
    connection.execute("DELETE FROM metric_definition")
    los_ids = [
        graph.edge("metric:los", predicate).id
        for predicate in ("admit_field", "discharge_field", "facility_field", "reporting_unit")
    ]
    connection.executemany(
        "INSERT INTO metric_definition VALUES (?, ?, ?, ?)",
        [
            ("los", graph.version, json.dumps(los_ids), as_of.isoformat()),
            ("readmission", graph.version, json.dumps(policy.fact_ids), as_of.isoformat()),
        ],
    )


@dataclass(frozen=True)
class Encounter:
    key: str
    csn: str
    patient: str
    facility_key: str
    facility_name: str
    admit: datetime
    discharge: datetime
    admit_kind: str
    admit_source: str
    disposition: str
    fact_ids: tuple[str, ...]

    @property
    def los_seconds(self) -> int:
        return int((self.discharge - self.admit).total_seconds())


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise ValueError(f"Invalid UTC timestamp: {value}; expected YYYY-MM-DDTHH:MM:SS") from exc
    return parsed


def _read_source(path: Path, columns: tuple[str, ...], key: str) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or tuple(reader.fieldnames) != columns:
            raise ValueError(f"{path.name}: expected columns {columns}")
        rows = list(reader)
    if not rows or any(None in row or any(value is None or not value.strip() for value in row.values()) for row in rows):
        raise ValueError(f"{path.name}: empty or incomplete source record")
    keys = [row[key] for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{path.name}: duplicate {key}")
    return rows


def _stage(connection: sqlite3.Connection, sources: dict[str, list[dict[str, str]]]) -> None:
    for table, (_, columns, key) in SOURCES.items():
        rows = sources[table]
        schema = ", ".join(f'"{column}" TEXT NOT NULL' for column in columns)
        connection.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({schema}, PRIMARY KEY ("{key}"))')
        connection.execute(f'DELETE FROM "{table}"')
        placeholders = ", ".join("?" for _ in columns)
        connection.executemany(
            f'INSERT INTO "{table}" VALUES ({placeholders})',
            [tuple(row[column] for column in columns) for row in rows],
        )


def _records(connection: sqlite3.Connection, table: str) -> list[dict[str, str]]:
    cursor = connection.execute(f'SELECT * FROM "{table}"')
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def _concept(graph: KnowledgeGraph, subject: str, allowed: set[str]) -> tuple[str, str]:
    fact = graph.edge(subject, "maps_to")
    if fact.object not in allowed:
        raise ValueError(f"Invalid graph concept {subject} -> {fact.object}")
    return fact.object, fact.id


def _encounters(connection: sqlite3.Connection, graph: KnowledgeGraph, as_of: datetime) -> list[Encounter]:
    clarity = {row["PAT_ENC_CSN_ID"]: row for row in _records(connection, "raw_clarity_pat_enc_hsp")}
    patients = {row["PatientDurableKey"]: row for row in _records(connection, "raw_caboodle_patient_dim")}
    facilities = {row["FacilityKey"]: row for row in _records(connection, "raw_caboodle_facility_dim")}
    facts = _records(connection, "raw_caboodle_encounter_fact")
    if len(facts) != len(clarity) or len({row["ClarityEncounterId"] for row in facts}) != len(clarity):
        raise ValueError("EncounterFact and PAT_ENC_HSP must have a one-to-one encounter join")
    if len({row["ClarityPatId"] for row in patients.values()}) != len(patients):
        raise ValueError("PatientDim maps more than one durable key to the same patient")
    result = []
    for fact in facts:
        key, csn = fact["EncounterKey"], fact["ClarityEncounterId"]
        if csn not in clarity or fact["PatientDurableKey"] not in patients or fact["FacilityKey"] not in facilities:
            raise ValueError(f"Orphan EncounterFact {key}")
        row = clarity[csn]
        if row["PAT_ID"] != patients[fact["PatientDurableKey"]]["ClarityPatId"]:
            raise ValueError(f"Patient mismatch for {key}")
        admit, discharge = _timestamp(row["HOSP_ADMSN_TIME"]), _timestamp(row["HOSP_DISCH_TIME"])
        if not admit < discharge <= as_of:
            raise ValueError(f"Invalid admission/discharge window for {key}")
        site = graph.edge(f"facility:{fact['FacilityKey']}", "maps_to")
        if not site.object.startswith("site:"):
            raise ValueError(f"Invalid facility graph target for {key}")
        name = graph.edge(site.object, "display_name")
        if name.object != facilities[fact["FacilityKey"]]["FacilityName"]:
            raise ValueError(f"FacilityDim disagrees with graph for {key}")
        kind, kind_id = _concept(graph, f"admit_type:{row['ADMIT_TYPE_C']}", {"planned", "unplanned"})
        source, source_id = _concept(graph, f"admit_source:{row['ADMIT_SOURCE_C']}", {"community", "transfer"})
        disposition, dispo_id = _concept(
            graph, f"disposition:{row['DISCH_DISPOSITION_C']}", {"home", "transfer", "expired"}
        )
        result.append(Encounter(
            key, csn, row["PAT_ID"], fact["FacilityKey"], name.object, admit, discharge,
            kind, source, disposition, (site.id, name.id, kind_id, source_id, dispo_id),
        ))
    by_patient: dict[str, list[Encounter]] = defaultdict(list)
    for encounter in result:
        by_patient[encounter.patient].append(encounter)
    for visits in by_patient.values():
        visits.sort(key=lambda visit: visit.admit)
        for previous, following in zip(visits, visits[1:]):
            if previous.discharge > following.admit:
                raise ValueError(f"Overlapping encounters: {previous.key}, {following.key}")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS encounter_mart ("
        "encounter_key TEXT PRIMARY KEY, clarity_csn TEXT NOT NULL, patient_id TEXT NOT NULL, "
        "facility_key TEXT NOT NULL, facility_name TEXT NOT NULL, admit_at TEXT NOT NULL, "
        "discharge_at TEXT NOT NULL, los_seconds INTEGER NOT NULL, admit_kind TEXT NOT NULL, "
        "admit_source TEXT NOT NULL, disposition TEXT NOT NULL, graph_version TEXT NOT NULL, "
        "fact_ids TEXT NOT NULL)"
    )
    connection.execute("DELETE FROM encounter_mart")
    connection.executemany(
        "INSERT INTO encounter_mart VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (v.key, v.csn, v.patient, v.facility_key, v.facility_name,
             v.admit.isoformat(), v.discharge.isoformat(), v.los_seconds,
             v.admit_kind, v.admit_source, v.disposition, graph.version, json.dumps(v.fact_ids))
            for v in result
        ],
    )
    return result


def _hours(seconds: int, count: int = 1) -> str:
    return str((Decimal(seconds) / Decimal(3600 * count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _los(connection: sqlite3.Connection, visits: list[Encounter], graph: KnowledgeGraph, as_of: datetime) -> list[dict]:
    grouped: dict[str, list[Encounter]] = defaultdict(list)
    for visit in visits:
        grouped[visit.facility_key].append(visit)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS facility_los ("
        "facility_key TEXT PRIMARY KEY, facility_name TEXT NOT NULL, discharged_count INTEGER NOT NULL, "
        "total_hours TEXT NOT NULL, average_hours TEXT NOT NULL, as_of TEXT NOT NULL, graph_version TEXT NOT NULL)"
    )
    connection.execute("DELETE FROM facility_los")
    result = []
    for key, group in sorted(grouped.items()):
        seconds = sum(visit.los_seconds for visit in group)
        row = {
            "facility_key": key, "facility_name": group[0].facility_name,
            "discharged_count": len(group), "total_hours": _hours(seconds),
            "average_hours": _hours(seconds, len(group)),
        }
        result.append(row)
        connection.execute(
            "INSERT INTO facility_los VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, row["facility_name"], row["discharged_count"], row["total_hours"],
             row["average_hours"], as_of.isoformat(), graph.version),
        )
    return result


def _readmissions(
    connection: sqlite3.Connection, visits: list[Encounter], graph: KnowledgeGraph, as_of: datetime,
    policy: ClinicalPolicy,
) -> list[dict]:
    by_patient: dict[str, list[Encounter]] = defaultdict(list)
    for visit in visits:
        by_patient[visit.patient].append(visit)
    for group in by_patient.values():
        group.sort(key=lambda visit: visit.admit)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS readmission_index ("
        "encounter_key TEXT PRIMARY KEY, facility_key TEXT NOT NULL, eligibility TEXT NOT NULL, "
        "readmission_encounter_key TEXT, as_of TEXT NOT NULL, graph_version TEXT NOT NULL)"
    )
    connection.execute("DELETE FROM readmission_index")
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for index in visits:
        if index.disposition != policy.index_disposition:
            eligibility = f"excluded_{index.disposition}"
        elif index.discharge + policy.window > as_of:
            eligibility = "immature"
        else:
            eligibility = "eligible"
        qualifying = None
        if eligibility == "eligible":
            counts[index.facility_key][0] += 1
            qualifying = next((
                candidate.key for candidate in by_patient[index.patient]
                if index.discharge < candidate.admit <= index.discharge + policy.window
                and candidate.admit_kind == policy.return_admit_kind
                and candidate.admit_source != policy.excluded_return_source
            ), None)
            if qualifying is not None:
                counts[index.facility_key][1] += 1
        connection.execute(
            "INSERT INTO readmission_index VALUES (?, ?, ?, ?, ?, ?)",
            (index.key, index.facility_key, eligibility, qualifying, as_of.isoformat(), graph.version),
        )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS facility_readmission ("
        "facility_key TEXT PRIMARY KEY, facility_name TEXT NOT NULL, eligible_discharges INTEGER NOT NULL, "
        "readmitted_discharges INTEGER NOT NULL, rate_pct TEXT, as_of TEXT NOT NULL, graph_version TEXT NOT NULL)"
    )
    connection.execute("DELETE FROM facility_readmission")
    result = []
    for key, name in sorted({v.facility_key: v.facility_name for v in visits}.items()):
        denominator, numerator = counts[key]
        rate = str((Decimal(numerator * 100) / denominator).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )) if denominator else None
        row = {"facility_key": key, "facility_name": name, "eligible_discharges": denominator,
               "readmitted_discharges": numerator, "rate_pct": rate}
        result.append(row)
        connection.execute(
            "INSERT INTO facility_readmission VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, name, denominator, numerator, rate, as_of.isoformat(), graph.version),
        )
    return result


def run_clinical_pipeline(
    data_dir: Path, graph_path: Path, db_path: Path, as_of: str
) -> dict:
    """Commit one clinical snapshot and its PROV-O history, then export its RDF."""
    started_at = now_utc()
    run_id = new_run_id()
    cutoff = _timestamp(as_of)
    graph = KnowledgeGraph(graph_path)
    policy = _metric_policy(graph)
    limits = quality_limits(graph)
    sources = {
        table: _read_source(data_dir / filename, columns, key)
        for table, (filename, columns, key) in SOURCES.items()
    }
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as connection, connection:
        _stage(connection, sources)
        visits = _encounters(connection, graph, cutoff)
        los = _los(connection, visits, graph, cutoff)
        readmissions = _readmissions(connection, visits, graph, cutoff, policy)
        _record_metric_facts(connection, graph, cutoff, policy)
        metric_facts = {
            row[0]: json.loads(row[1])
            for row in connection.execute("SELECT metric, fact_ids FROM metric_definition")
        }
        metric_facts["quality"] = [
            graph.edge("quality:clinical", predicate).id for predicate in QUALITY_PREDICATES
        ]
        quality_checks = {
            "mapping_coverage": 100 >= limits["min_mapping_coverage_pct"],
            "missing_required": 0 <= limits["max_missing_required_pct"],
            "orphan_joins": 0 <= limits["max_orphan_joins"],
            "eligible_per_facility": bool(readmissions) and all(
                row["eligible_discharges"] >= limits["min_eligible_per_facility"]
                for row in readmissions
            ),
        }
        quality_status = "PASS" if all(quality_checks.values()) else "FAIL"
        archive_run(
            connection, run_id=run_id, started_at=started_at, ended_at=now_utc(),
            as_of=cutoff.isoformat() + "+00:00", graph_version=graph.version,
            graph_sha256=graph_hash(graph.version, graph.facts), code_sha256=code_hash(),
            source_hashes={table: snapshot_hash(rows) for table, rows in sources.items()},
            metrics={"facility_los": los, "facility_readmission": readmissions},
            metric_fact_ids=metric_facts, quality_status=quality_status,
            graph_facts=[vars(fact) for fact in graph.facts],
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS clinical_current_run ("
            "id INTEGER PRIMARY KEY CHECK(id = 1), run_id TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO clinical_current_run (id, run_id) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET run_id = excluded.run_id", (run_id,)
        )
    # SQLite is authoritative: export failures are raised and the archived graph can be re-exported.
    provenance_path = export_run_provenance(db_path, run_id)
    return {
        "graph_version": graph.version, "as_of": cutoff.isoformat(),
        "encounters": len(visits), "facility_los": los, "facility_readmission": readmissions,
        "run_id": run_id, "provenance_path": str(provenance_path),
        "quality_status": quality_status,
    }
