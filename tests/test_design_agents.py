import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage

from grounded_elt.cli import ROOT, main
from grounded_elt.design_agents import (
    MAPPINGS, _last_json, mapping_evidence, quality_decision, quality_evidence,
    reference_recipes, run_design_agents, save_design, validate_recipes,
)
from grounded_elt.pipeline import KnowledgeGraph


class FakeAgent:
    def __init__(self, answer, tools):
        self.answer = answer
        self.tools = tools

    def invoke(self, request):
        self.tools[0]()
        return {"messages": [AIMessage(content=json.dumps(self.answer))]}


class DesignAgentsTests(unittest.TestCase):
    def setUp(self):
        self.graph = ROOT / "data/knowledge_graph.json"
        self.data = ROOT / "data/clinical"
        self.as_of = "2026-05-31T00:00:00"
        self.reference = reference_recipes(KnowledgeGraph(self.graph))

    def test_three_distinct_agents_and_review_only_artifacts(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)

            def factory(*, model, tools, system_prompt):
                calls.append((model, system_prompt, tools[0].__name__))
                if len(calls) == 1:
                    return FakeAgent({"graph_version": "clinical-demo-v2", "target_mappings": MAPPINGS}, tools)
                if len(calls) == 2:
                    return FakeAgent(self.reference, tools)
                return FakeAgent(
                    {"status": "PASS", "checks": {key: True for key in (
                        "mapping_coverage", "missing_required", "orphan_joins", "eligible_per_facility"
                    )}}, tools,
                )

            result = run_design_agents(self.data, self.graph, self.as_of, "stub:model", factory)
            self.assertEqual([call[2] for call in calls], [
                "inspect_mapping_contract", "inspect_recipe_inputs", "inspect_quality_evidence"
            ])
            self.assertEqual(result["quality"]["status"], "PASS")
            self.assertEqual(result["quality"]["evidence"]["eligible_by_facility"], {
                "North Hospital": 6, "South Hospital": 2
            })
            self.assertEqual(list(path.iterdir()), [])
            save_design(result, path / "review")
            self.assertEqual(sorted(item.name for item in (path / "review").iterdir()), [
                "encounter_sql.sql", "los_sql.sql", "mapping.json", "quality.json",
                "readmission_sql.sql", "recipe_parameters.json"
            ])
            self.assertEqual((path / "review" / "los_sql.sql").read_text(), self.reference["los_sql"] + "\n")

    def test_invalid_recipe_rejected_without_sql_execution(self):
        with self.assertRaisesRegex(ValueError, "one SELECT"):
            validate_recipes({**self.reference, "los_sql": "DROP TABLE encounter_mart"}, self.reference)
        with self.assertRaisesRegex(ValueError, "all four source"):
            validate_recipes({**self.reference, "encounter_sql": "SELECT * FROM raw_clarity_pat_enc_hsp"},
                             self.reference)
        with self.assertRaisesRegex(ValueError, "reference semantics"):
            validate_recipes({**self.reference, "readmission_sql": self.reference["readmission_sql"].replace(
                "r.patient_id = i.patient_id", "r.patient_id = i.patient_id AND r.facility_key = i.facility_key"
            )}, self.reference)
        with self.assertRaisesRegex(ValueError, "reference semantics"):
            validate_recipes({**self.reference, "los_sql": "SELECT nonexistent FROM encounter_mart"},
                             self.reference)

    def test_quality_threshold_changes_verdict_without_changing_metrics(self):
        evidence = quality_evidence(self.data, self.graph, self.as_of)
        self.assertEqual(quality_decision(evidence)["status"], "PASS")
        evidence["thresholds"]["min_eligible_per_facility"] = 3
        result = quality_decision(evidence)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["checks"]["eligible_per_facility"])

    def test_unknown_threshold_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.json"
            graph = json.loads(self.graph.read_text())
            graph["facts"] = [f for f in graph["facts"] if f["id"] != "quality-volume"]
            path.write_text(json.dumps(graph))
            with self.assertRaisesRegex(ValueError, "Missing graph fact"):
                quality_evidence(self.data, path, self.as_of)

    def test_mapping_contract_uses_current_graph_and_source_schema(self):
        manifest = mapping_evidence(KnowledgeGraph(self.graph))
        self.assertEqual(manifest["target_mappings"], MAPPINGS)
        self.assertIn("raw_clarity_pat_enc_hsp", manifest["source_schemas"])
        self.assertIn("readmit-window", [fact["id"] for fact in manifest["graph_facts"]])

    def test_graph_change_updates_reference_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.json"
            graph = json.loads(self.graph.read_text())
            next(f for f in graph["facts"] if f["id"] == "admit-emerg")["object"] = "planned"
            path.write_text(json.dumps(graph))
            updated = reference_recipes(KnowledgeGraph(path))
            self.assertNotEqual(updated["encounter_sql"], self.reference["encounter_sql"])
            with self.assertRaisesRegex(ValueError, "reference semantics"):
                validate_recipes(self.reference, updated)

    def test_gemini_text_block_and_code_fence_are_parsed(self):
        response = {"messages": [AIMessage(content=[
            {"type": "text", "text": '```json\n{"status": "PASS"}\n```'}
        ])]}
        self.assertEqual(_last_json(response), {"status": "PASS"})

    def test_quality_failure_exits_nonzero_after_saving_report(self):
        report = {"mapping": {"graph_version": "clinical-demo-v2"}, "quality": {"status": "FAIL"}}
        with patch("sys.argv", ["cli", "--design-agents"]), \
             patch("grounded_elt.cli.run_design_agents", return_value=report), \
             patch("grounded_elt.cli.save_design") as save:
            with self.assertRaisesRegex(SystemExit, "Quality gate failed"):
                main()
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
