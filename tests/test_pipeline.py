import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from grounded_elt.cli import ROOT, build_agent
from grounded_elt.pipeline import run_pipeline


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.orders = self.base / "orders.csv"
        self.graph = self.base / "graph.json"
        self.db = self.base / "warehouse.sqlite"
        self.orders.write_bytes((ROOT / "data/orders.csv").read_bytes())
        self.graph.write_bytes((ROOT / "data/sales_knowledge_graph.json").read_bytes())

    def test_sample_and_idempotent_rerun_with_lineage(self):
        expected = {"raw_rows": 3, "mart_rows": 3, "revenue_usd_cents": 4970, "graph_version": "demo-v1"}
        self.assertEqual(run_pipeline(self.orders, self.graph, self.db), expected)
        self.assertEqual(run_pipeline(self.orders, self.graph, self.db), expected)
        with sqlite3.connect(self.db) as connection:
            raw = connection.execute("SELECT count(*) FROM raw_orders").fetchone()[0]
            mart = connection.execute(
                "SELECT order_id, family, revenue_usd_cents, fact_ids FROM sales_mart ORDER BY order_id"
            ).fetchall()
        self.assertEqual(raw, 3)
        self.assertEqual([(r[0], r[1], r[2]) for r in mart], [
            ("O-1", "hardware", 2000),
            ("O-2", "software", 1650),
            ("O-3", "hardware", 1320),
        ])
        self.assertEqual(json.loads(mart[1][3]), ["p-b", "c-lic", "fx-eur"])

    def test_changed_graph_fact_changes_result(self):
        graph = json.loads(self.graph.read_text())
        graph["version"] = "demo-v2"
        next(f for f in graph["facts"] if f["id"] == "fx-eur")["object"] = "1.20"
        self.graph.write_text(json.dumps(graph))
        result = run_pipeline(self.orders, self.graph, self.db)
        self.assertEqual(result["revenue_usd_cents"], 5240)
        self.assertEqual(result["graph_version"], "demo-v2")

    def test_bad_graph_does_not_replace_previous_success(self):
        run_pipeline(self.orders, self.graph, self.db)
        graph = json.loads(self.graph.read_text())
        graph["facts"] = [f for f in graph["facts"] if f["id"] != "p-b"]
        self.graph.write_text(json.dumps(graph))
        with self.assertRaisesRegex(ValueError, "Missing graph fact"):
            run_pipeline(self.orders, self.graph, self.db)
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT sum(revenue_usd_cents) FROM sales_mart").fetchone()[0], 4970)

    def test_ambiguous_graph_rejected(self):
        graph = json.loads(self.graph.read_text())
        graph["facts"].append({
            "id": "p-a-duplicate", "subject": "product:SKU-A",
            "predicate": "belongs_to", "object": "category:licenses",
        })
        self.graph.write_text(json.dumps(graph))
        with self.assertRaisesRegex(ValueError, "Ambiguous graph fact"):
            run_pipeline(self.orders, self.graph, self.db)

    def test_invalid_csv_rejected(self):
        self.orders.write_text(
            "order_id,product_sku,quantity,unit_price,currency\n"
            "O-1,SKU-A,2,10.00,USD\nO-1,SKU-B,1,15.00,EUR\n"
        )
        with self.assertRaisesRegex(ValueError, "duplicate order IDs"):
            run_pipeline(self.orders, self.graph, self.db)
        self.assertFalse(self.db.exists())

    def test_agent_builds_with_domain_tools(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only-placeholder"}):
            agent, completed = build_agent(self.orders, self.graph, self.db, "openai:gpt-4.1-mini")
        self.assertIsNotNone(agent)
        self.assertEqual(completed, [])
        self.assertFalse(self.db.exists())

    def test_invalid_price_rejected_without_replacing_mart(self):
        run_pipeline(self.orders, self.graph, self.db)
        self.orders.write_text(
            "order_id,product_sku,quantity,unit_price,currency\nO-1,SKU-A,2,NaN,USD\n"
        )
        with self.assertRaisesRegex(ValueError, "Invalid quantity or price"):
            run_pipeline(self.orders, self.graph, self.db)
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM raw_orders").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
