import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rdflib import Graph, Literal, Namespace, RDF
from rdflib.namespace import PROV

from grounded_elt.cli import ROOT
from grounded_elt.clinical import run_clinical_pipeline
from grounded_elt.provenance import POC, export_run_provenance


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.data = self.base / "clinical"
        self.data.mkdir()
        for file in (ROOT / "data/clinical").iterdir():
            (self.data / file.name).write_bytes(file.read_bytes())
        self.graph_path = self.base / "graph.json"
        self.graph_path.write_bytes((ROOT / "data/knowledge_graph.json").read_bytes())
        self.db = self.base / "warehouse.sqlite"

    def run_etl(self):
        return run_clinical_pipeline(self.data, self.graph_path, self.db, "2026-05-31T00:00:00")

    def test_prov_o_links_source_staging_normalization_rules_and_facility_metrics(self):
        result = self.run_etl()
        path = Path(result["provenance_path"])
        self.assertTrue(path.is_file())
        rdf = Graph().parse(path, format="turtle")
        base = Namespace(f"urn:grounded-elt:run:{result['run_id']}:")
        self.assertIn((base["activity/run"], RDF.type, PROV.Activity), rdf)
        self.assertIn((base["activity/stage"], PROV.used,
                       base["entity/source/raw_clarity_pat_enc_hsp"]), rdf)
        self.assertIn((base["entity/raw-staging"], PROV.wasGeneratedBy,
                       base["activity/stage"]), rdf)
        self.assertIn((base["entity/encounter-mart"], PROV.wasDerivedFrom,
                       base["entity/raw-staging"]), rdf)
        self.assertIn((base["activity/calculate-readmission"], PROV.used,
                       base["entity/rule/readmit-window"]), rdf)
        self.assertIn((base["entity/metric/readmission/1"], PROV.wasGeneratedBy,
                       base["activity/calculate-readmission"]), rdf)
        self.assertIn((base["entity/metric/los/2"], PROV.wasDerivedFrom,
                       base["entity/encounter-mart"]), rdf)
        self.assertIn((base["entity/metric/readmission/1"], POC.facilityKey, Literal("1")), rdf)
        self.assertIn((base["entity/metric/readmission/1"], POC.readmitted_discharges, Literal(3)), rdf)
        self.assertIn((base["entity/clinical-graph"], POC.version, Literal("clinical-demo-v2")), rdf)
        self.assertIn((base["entity/quality-report"], POC.status, Literal("PASS")), rdf)
        self.assertIn((base["activity/check-quality"], PROV.used,
                       base["entity/rule/quality-volume"]), rdf)
        self.assertIn((base["entity/rule/readmit-window"], POC.value, Literal("30")), rdf)
        text = path.read_text()
        self.assertNotIn("PAT_ID", text)
        self.assertNotIn("PatientDurableKey", text)
        self.assertNotIn("E101", text)
        self.assertNotIn("P1", text)
        with sqlite3.connect(self.db) as connection:
            row = connection.execute(
                "SELECT graph_sha256, code_sha256, source_hashes, aggregate_snapshot, "
                "prov_turtle, quality_status, graph_snapshot "
                "FROM clinical_run_history WHERE run_id=?", (result["run_id"],)
            ).fetchone()
            current = connection.execute("SELECT run_id FROM clinical_current_run").fetchone()[0]
        self.assertEqual(current, result["run_id"])
        self.assertEqual(row[5], "PASS")
        self.assertEqual(json.loads(row[6])["version"], "clinical-demo-v2")
        self.assertEqual(row[4], text)
        self.assertEqual(len(row[0]), 64)
        self.assertEqual(len(row[1]), 64)
        self.assertEqual(len(json.loads(row[2])), 4)
        self.assertEqual(json.loads(row[3])["facility_readmission"][0]["readmitted_discharges"], 3)

    def test_history_survives_reruns_and_source_and_graph_changes(self):
        first = self.run_etl()
        file = self.data / "clarity_pat_enc_hsp.csv"
        with file.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["ADMIT_TYPE_C"] = "SCHED"
        with file.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        second = self.run_etl()
        self.assertNotEqual(first["run_id"], second["run_id"])
        with sqlite3.connect(self.db) as connection:
            source_hashes = {
                run_id: json.loads(hashes)
                for run_id, hashes in connection.execute(
                    "SELECT run_id, source_hashes FROM clinical_run_history"
                )
            }
            self.assertEqual(connection.execute("SELECT count(*) FROM clinical_run_history").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT count(*) FROM encounter_mart").fetchone()[0], 9)
            self.assertEqual(connection.execute("SELECT run_id FROM clinical_current_run").fetchone()[0],
                             second["run_id"])
        self.assertNotEqual(
            source_hashes[first["run_id"]]["raw_clarity_pat_enc_hsp"],
            source_hashes[second["run_id"]]["raw_clarity_pat_enc_hsp"],
        )
        self.assertTrue(Path(first["provenance_path"]).exists())
        self.assertTrue(Path(second["provenance_path"]).exists())

    def test_failed_validation_does_not_archive_or_export(self):
        prior = self.run_etl()
        file = self.data / "clarity_pat_enc_hsp.csv"
        text = file.read_text()
        file.write_text(text.replace("2026-01-03T10:00:00", "2025-01-03T10:00:00"))
        with self.assertRaisesRegex(ValueError, "Invalid admission/discharge window"):
            self.run_etl()
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM clinical_run_history").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM encounter_mart").fetchone()[0], 9)
            self.assertEqual(connection.execute("SELECT run_id FROM clinical_current_run").fetchone()[0],
                             prior["run_id"])
        self.assertEqual(len(list(self.db.with_suffix(".provenance").glob("*.ttl"))), 1)
        self.assertTrue(Path(prior["provenance_path"]).exists())

    def test_export_failure_surfaces_and_archived_rdf_can_be_recovered(self):
        with patch("grounded_elt.clinical.export_run_provenance", side_effect=PermissionError("read-only")):
            with self.assertRaisesRegex(PermissionError, "read-only"):
                self.run_etl()
        with sqlite3.connect(self.db) as connection:
            run_id = connection.execute("SELECT run_id FROM clinical_run_history").fetchone()[0]
        path = export_run_provenance(self.db, run_id)
        self.assertTrue(path.is_file())
        self.assertEqual(export_run_provenance(self.db, run_id), path)
        with self.assertRaisesRegex(ValueError, "Unknown clinical run"):
            export_run_provenance(self.db, "not-a-run")

    def test_failed_quality_check_is_recorded_not_misreported_as_pass(self):
        graph = json.loads(self.graph_path.read_text())
        next(f for f in graph["facts"] if f["id"] == "quality-volume")["object"] = "10"
        self.graph_path.write_text(json.dumps(graph))
        result = self.run_etl()
        self.assertEqual(result["quality_status"], "FAIL")
        rdf = Graph().parse(result["provenance_path"], format="turtle")
        base = Namespace(f"urn:grounded-elt:run:{result['run_id']}:")
        self.assertIn((base["entity/quality-report"], POC.status, Literal("FAIL")), rdf)
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute(
                "SELECT quality_status FROM clinical_run_history WHERE run_id=?", (result["run_id"],)
            ).fetchone()[0], "FAIL")

    def test_changed_rule_graph_changes_hash_and_keeps_prior_graph(self):
        first = self.run_etl()
        graph = json.loads(self.graph_path.read_text())
        graph["version"] = "clinical-demo-v3"
        next(f for f in graph["facts"] if f["id"] == "readmit-window")["object"] = "10"
        self.graph_path.write_text(json.dumps(graph))
        second = self.run_etl()
        with sqlite3.connect(self.db) as connection:
            snapshots = {
                run_id: (version, digest, json.loads(metrics), json.loads(snapshot))
                for run_id, version, digest, metrics, snapshot in connection.execute(
                    "SELECT run_id, graph_version, graph_sha256, aggregate_snapshot, graph_snapshot "
                    "FROM clinical_run_history"
                )
            }
        self.assertNotEqual(snapshots[first["run_id"]][1], snapshots[second["run_id"]][1])
        self.assertEqual(snapshots[first["run_id"]][0], "clinical-demo-v2")
        self.assertEqual(snapshots[second["run_id"]][0], "clinical-demo-v3")
        self.assertEqual(snapshots[first["run_id"]][2]["facility_readmission"][0]["readmitted_discharges"], 3)
        self.assertEqual(snapshots[second["run_id"]][2]["facility_readmission"][0]["readmitted_discharges"], 0)
        self.assertEqual(
            next(f for f in snapshots[first["run_id"]][3]["facts"] if f["id"] == "readmit-window")["object"], "30"
        )
        self.assertEqual(
            next(f for f in snapshots[second["run_id"]][3]["facts"] if f["id"] == "readmit-window")["object"], "10"
        )

    def test_previous_archive_adds_snapshot_column_without_inventing_history(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "CREATE TABLE clinical_run_history (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, "
                "ended_at TEXT NOT NULL, as_of TEXT NOT NULL, graph_version TEXT NOT NULL, "
                "graph_sha256 TEXT NOT NULL, code_sha256 TEXT NOT NULL, source_hashes TEXT NOT NULL, "
                "metric_fact_ids TEXT NOT NULL, aggregate_snapshot TEXT NOT NULL, "
                "quality_status TEXT NOT NULL, prov_turtle TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO clinical_run_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("prior-run", "start", "end", "cutoff", "prior", "oldhash", "oldcode",
                 "{}", "{}", "{}", "PASS", "prior rdf"),
            )
        current = self.run_etl()
        with sqlite3.connect(self.db) as connection:
            self.assertIsNone(connection.execute(
                "SELECT graph_snapshot FROM clinical_run_history WHERE run_id='prior-run'"
            ).fetchone()[0])
            self.assertEqual(json.loads(connection.execute(
                "SELECT graph_snapshot FROM clinical_run_history WHERE run_id=?", (current["run_id"],)
            ).fetchone()[0])["version"], "clinical-demo-v2")


if __name__ == "__main__":
    unittest.main()
