import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grounded_elt.cli import ROOT, build_clinical_agent
from grounded_elt.clinical import run_clinical_pipeline


class ClinicalPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.data = self.base / "clinical"
        self.data.mkdir()
        for file in (ROOT / "data/clinical").iterdir():
            (self.data / file.name).write_bytes(file.read_bytes())
        self.graph = self.base / "knowledge_graph.json"
        self.graph.write_bytes((ROOT / "data/knowledge_graph.json").read_bytes())
        self.db = self.base / "clinical.sqlite"
        self.cutoff = "2026-05-31T00:00:00"

    def run_etl(self, as_of=None):
        return run_clinical_pipeline(self.data, self.graph, self.db, as_of or self.cutoff)

    def change_csv(self, filename, action):
        file = self.data / filename
        with file.open(newline="") as source:
            rows = list(csv.DictReader(source))
        action(rows)
        with file.open("w", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_facility_los_and_readmissions_are_grounded_and_repeatable(self):
        first = self.run_etl()
        second = self.run_etl()
        self.assertNotEqual(second["run_id"], first["run_id"])
        self.assertEqual(
            {key: value for key, value in second.items() if key not in ("run_id", "provenance_path")},
            {key: value for key, value in first.items() if key not in ("run_id", "provenance_path")},
        )
        self.assertEqual(first["encounters"], 9)
        north, south = first["facility_los"]
        self.assertEqual((north["discharged_count"], north["average_hours"], north["total_hours"]), (7, "44.57", "312.00"))
        self.assertEqual((south["discharged_count"], south["average_hours"]), (2, "48.00"))
        self.assertEqual([
            (row["eligible_discharges"], row["readmitted_discharges"], row["rate_pct"])
            for row in first["facility_readmission"]
        ], [(6, 3, "50.00"), (2, 0, "0.00")])
        with sqlite3.connect(self.db) as connection:
            for table, size in (
                ("raw_clarity_pat_enc_hsp", 9), ("raw_caboodle_encounter_fact", 9),
                ("raw_caboodle_patient_dim", 3), ("raw_caboodle_facility_dim", 2),
                ("encounter_mart", 9), ("facility_los", 2),
                ("readmission_index", 9), ("facility_readmission", 2), ("metric_definition", 2),
            ):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], size)
            fact_ids = connection.execute("SELECT fact_ids FROM encounter_mart WHERE encounter_key='E101'").fetchone()[0]
            self.assertEqual(json.loads(fact_ids), [
                "site-2", "name-south", "admit-emerg", "source-direct", "disch-home"
            ])
            index = connection.execute(
                "SELECT encounter_key, eligibility, readmission_encounter_key "
                "FROM readmission_index ORDER BY encounter_key"
            ).fetchall()
            policy = connection.execute(
                "SELECT graph_version, fact_ids FROM metric_definition WHERE metric='readmission'"
            ).fetchone()
        self.assertEqual(policy[0], "clinical-demo-v2")
        self.assertEqual(json.loads(policy[1]), [
            "readmit-window", "readmit-index", "readmit-return", "readmit-exclude", "readmit-attribution"
        ])
        self.assertEqual(index, [
            ("E100", "eligible", "E101"), ("E101", "eligible", None),
            ("E102", "eligible", "E103"), ("E103", "eligible", None),
            ("E200", "excluded_transfer", None), ("E201", "eligible", None),
            ("E300", "eligible", None), ("E301", "eligible", "E302"),
            ("E302", "eligible", None),
        ])

    def test_insufficient_followup_excludes_recent_discharge(self):
        result = self.run_etl("2026-05-01T00:00:00")
        self.assertEqual(result["facility_readmission"][0]["eligible_discharges"], 5)
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute(
                "SELECT eligibility FROM readmission_index WHERE encounter_key='E103'"
            ).fetchone()[0], "immature")

    def test_transfer_source_does_not_count_as_return(self):
        self.change_csv(
            "clarity_pat_enc_hsp.csv",
            lambda rows: next(r for r in rows if r["PAT_ENC_CSN_ID"] == "101").update(
                {"ADMIT_SOURCE_C": "TRANSFER"}
            ),
        )
        result = self.run_etl()
        self.assertEqual(result["facility_readmission"][0]["readmitted_discharges"], 2)
        with sqlite3.connect(self.db) as connection:
            self.assertIsNone(connection.execute(
                "SELECT readmission_encounter_key FROM readmission_index WHERE encounter_key='E100'"
            ).fetchone()[0])

    def test_exact_30_day_boundary(self):
        def modify(rows):
            encounter = next(row for row in rows if row["PAT_ENC_CSN_ID"] == "101")
            encounter["HOSP_ADMSN_TIME"] = "2026-02-02T10:00:00"
            encounter["HOSP_DISCH_TIME"] = "2026-02-04T10:00:00"

        self.change_csv("clarity_pat_enc_hsp.csv", modify)
        self.run_etl()
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute(
                "SELECT readmission_encounter_key FROM readmission_index WHERE encounter_key='E100'"
            ).fetchone()[0], "E101")
        def outside(rows):
            encounter = next(row for row in rows if row["PAT_ENC_CSN_ID"] == "101")
            encounter["HOSP_ADMSN_TIME"] = "2026-02-02T10:00:01"

        self.change_csv("clarity_pat_enc_hsp.csv", outside)
        self.run_etl()
        with sqlite3.connect(self.db) as connection:
            self.assertIsNone(connection.execute(
                "SELECT readmission_encounter_key FROM readmission_index WHERE encounter_key='E100'"
            ).fetchone()[0])

    def test_graph_mapping_changes_rate_and_lineage(self):
        graph = json.loads(self.graph.read_text())
        graph["version"] = "clinical-demo-v3"
        next(f for f in graph["facts"] if f["id"] == "admit-emerg")["object"] = "planned"
        self.graph.write_text(json.dumps(graph))
        self.assertEqual([r["readmitted_discharges"] for r in self.run_etl()["facility_readmission"]], [0, 0])
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute(
                "SELECT graph_version, admit_kind FROM encounter_mart WHERE encounter_key='E100'"
            ).fetchone(), ("clinical-demo-v3", "planned"))

    def test_graph_readmission_window_changes_cohort_and_outcomes(self):
        graph = json.loads(self.graph.read_text())
        graph["version"] = "clinical-demo-v3"
        next(f for f in graph["facts"] if f["id"] == "readmit-window")["object"] = "10"
        self.graph.write_text(json.dumps(graph))
        result = self.run_etl()
        self.assertEqual(result["facility_readmission"][0]["readmitted_discharges"], 0)
        self.assertEqual(result["facility_readmission"][0]["eligible_discharges"], 6)
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute(
                "SELECT graph_version FROM metric_definition WHERE metric='readmission'"
            ).fetchone()[0], "clinical-demo-v3")

    def test_missing_or_unsupported_metric_fact_preserves_previous_snapshot(self):
        self.run_etl()
        graph = json.loads(self.graph.read_text())
        graph["facts"] = [f for f in graph["facts"] if f["id"] != "readmit-window"]
        self.graph.write_text(json.dumps(graph))
        with self.assertRaisesRegex(ValueError, "Missing graph fact"):
            self.run_etl()
        graph["facts"].append({
            "id": "readmit-window", "subject": "metric:readmission",
            "predicate": "window_days", "object": "0",
        })
        self.graph.write_text(json.dumps(graph))
        with self.assertRaisesRegex(ValueError, "Invalid readmission window_days"):
            self.run_etl()
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute(
                "SELECT readmitted_discharges FROM facility_readmission WHERE facility_key='1'"
            ).fetchone()[0], 3)
            self.assertEqual(connection.execute(
                "SELECT graph_version FROM metric_definition WHERE metric='readmission'"
            ).fetchone()[0], "clinical-demo-v2")

    def test_unsupported_los_source_fact_rejected(self):
        graph = json.loads(self.graph.read_text())
        next(f for f in graph["facts"] if f["id"] == "los-admit-field")["object"] = "clarity:OTHER"
        self.graph.write_text(json.dumps(graph))
        with self.assertRaisesRegex(ValueError, "Unsupported LOS definition"):
            self.run_etl()

    def test_bad_join_rolls_back_previous_snapshot(self):
        self.run_etl()
        self.change_csv(
            "caboodle_encounter_fact.csv",
            lambda rows: rows[0].update({"PatientDurableKey": "D2"}),
        )
        with self.assertRaisesRegex(ValueError, "Patient mismatch"):
            self.run_etl()
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute(
                "SELECT PatientDurableKey FROM raw_caboodle_encounter_fact WHERE EncounterKey='E100'"
            ).fetchone()[0], "D1")
            self.assertEqual(connection.execute(
                "SELECT readmitted_discharges FROM facility_readmission WHERE Facility_key='1'"
            ).fetchone()[0], 3)

    def test_unknown_or_ambiguous_graph_fact_rolls_back(self):
        self.run_etl()
        graph = json.loads(self.graph.read_text())
        graph["facts"] = [f for f in graph["facts"] if f["id"] != "site-2"]
        self.graph.write_text(json.dumps(graph))
        with self.assertRaisesRegex(ValueError, "Missing graph fact"):
            self.run_etl()
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM encounter_mart").fetchone()[0], 9)
        graph["facts"].append({
            "id": "site-2a", "subject": "facility:2", "predicate": "maps_to", "object": "site:south"
        })
        graph["facts"].append({
            "id": "site-2b", "subject": "facility:2", "predicate": "maps_to", "object": "site:north"
        })
        self.graph.write_text(json.dumps(graph))
        with self.assertRaisesRegex(ValueError, "Ambiguous graph fact"):
            self.run_etl()

    def test_overlapping_patient_encounters_rejected(self):
        self.change_csv(
            "clarity_pat_enc_hsp.csv",
            lambda rows: next(r for r in rows if r["PAT_ENC_CSN_ID"] == "101").update(
                {"HOSP_ADMSN_TIME": "2026-01-02T10:00:00"}
            ),
        )
        with self.assertRaisesRegex(ValueError, "Overlapping encounters"):
            self.run_etl()

    def test_invalid_timestamp_and_unknown_code_are_rejected(self):
        self.change_csv(
            "clarity_pat_enc_hsp.csv",
            lambda rows: rows[0].update({"HOSP_DISCH_TIME": "2026-01-01T09:00:00"}),
        )
        with self.assertRaisesRegex(ValueError, "Invalid admission/discharge window"):
            self.run_etl()
        self.change_csv(
            "clarity_pat_enc_hsp.csv",
            lambda rows: rows[0].update({
                "HOSP_DISCH_TIME": "2026-01-03T10:00:00", "ADMIT_TYPE_C": "UNKNOWN",
            }),
        )
        with self.assertRaisesRegex(ValueError, "Missing graph fact"):
            self.run_etl()

    def test_no_eligible_discharge_has_null_rate(self):
        graph = json.loads(self.graph.read_text())
        next(f for f in graph["facts"] if f["id"] == "disch-home")["object"] = "transfer"
        self.graph.write_text(json.dumps(graph))
        self.assertEqual(
            [(r["eligible_discharges"], r["rate_pct"]) for r in self.run_etl()["facility_readmission"]],
            [(0, None), (0, None)],
        )

    def test_agent_construction_needs_no_network(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only-placeholder"}):
            agent, completed = build_clinical_agent(
                self.data, self.graph, self.db, self.cutoff, "openai:gpt-4.1-mini"
            )
        self.assertIsNotNone(agent)
        self.assertEqual(completed, [])
        self.assertFalse(self.db.exists())

    def test_gemini_agent_construction_needs_no_network(self):
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-only-placeholder"}):
            agent, completed = build_clinical_agent(
                self.data, self.graph, self.db, self.cutoff, "google_genai:gemini-2.5-flash"
            )
        self.assertIsNotNone(agent)
        self.assertEqual(completed, [])
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
