"""Deterministic CSV -> SQLite ELT grounded in a small triple graph."""

import csv
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


@dataclass(frozen=True)
class Fact:
    id: str
    subject: str
    predicate: str
    object: str


class KnowledgeGraph:
    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.version = data["version"]
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("Knowledge graph version must be a nonempty string")
        self.facts = [Fact(**fact) for fact in data["facts"]]
        if not self.facts or any(not all((f.id, f.subject, f.predicate, f.object)) for f in self.facts):
            raise ValueError("Knowledge graph must contain complete facts")
        if len({fact.id for fact in self.facts}) != len(self.facts):
            raise ValueError("Knowledge graph contains duplicate fact IDs")
        self.edges: dict[tuple[str, str], Fact] = {}
        for fact in self.facts:
            key = (fact.subject, fact.predicate)
            if key in self.edges:
                raise ValueError(f"Ambiguous graph fact for {key}")
            self.edges[key] = fact

    def edge(self, subject: str, predicate: str) -> Fact:
        try:
            return self.edges[(subject, predicate)]
        except KeyError as exc:
            raise ValueError(f"Missing graph fact: {subject} {predicate}") from exc

    def ground(self, sku: str, currency: str) -> tuple[str, Decimal, list[str]]:
        product = self.edge(f"product:{sku}", "belongs_to")
        category = self.edge(product.object, "reports_as")
        rate_fact = self.edge(f"currency:{currency}", "usd_rate")
        if not product.object.startswith("category:"):
            raise ValueError(f"Invalid category target: {product.object}")
        try:
            rate = Decimal(rate_fact.object)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid USD rate: {rate_fact.object}") from exc
        if not rate.is_finite() or rate <= 0:
            raise ValueError(f"Invalid USD rate: {rate_fact.object}")
        return category.object, rate, [product.id, category.id, rate_fact.id]


def read_orders(path: Path) -> list[dict[str, str]]:
    required = {"order_id", "product_sku", "quantity", "unit_price", "currency"}
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or set(reader.fieldnames) != required:
            raise ValueError(f"CSV headers must be {sorted(required)}")
        rows = list(reader)
    if not rows:
        raise ValueError("Order CSV is empty")
    if any(None in row or any(value is None or not value.strip() for value in row.values()) for row in rows):
        raise ValueError("Order CSV contains missing or empty fields")
    ids = [row["order_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Order CSV contains duplicate order IDs")
    return rows


def _transform(rows: list[dict[str, str]], graph: KnowledgeGraph) -> list[tuple[str, str, int, str, str]]:
    transformed = []
    for row in rows:
        try:
            quantity = int(row["quantity"])
            price = Decimal(row["unit_price"])
        except (ValueError, InvalidOperation) as exc:
            raise ValueError(f"Invalid quantity or price for {row['order_id']}") from exc
        if quantity <= 0 or not price.is_finite() or price < 0 or price != price.quantize(Decimal("0.01")):
            raise ValueError(f"Invalid quantity or price for {row['order_id']}")
        family, rate, fact_ids = graph.ground(row["product_sku"], row["currency"])
        amount = (price * quantity * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        cents = int(amount * 100)
        transformed.append((row["order_id"], family, cents, graph.version, json.dumps(fact_ids)))
    return transformed


def run_pipeline(orders_path: Path, graph_path: Path, db_path: Path) -> dict[str, int | str]:
    """Load raw orders, transform from staging, and commit the snapshot atomically."""
    graph = KnowledgeGraph(graph_path)
    rows = read_orders(orders_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS raw_orders ("
            "order_id TEXT PRIMARY KEY, product_sku TEXT NOT NULL, quantity TEXT NOT NULL, "
            "unit_price TEXT NOT NULL, currency TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS sales_mart ("
            "order_id TEXT PRIMARY KEY, family TEXT NOT NULL, revenue_usd_cents INTEGER NOT NULL, "
            "graph_version TEXT NOT NULL, fact_ids TEXT NOT NULL)"
        )
        connection.execute("DELETE FROM sales_mart")
        connection.execute("DELETE FROM raw_orders")
        connection.executemany(
            "INSERT INTO raw_orders VALUES (?, ?, ?, ?, ?)",
            [(r["order_id"], r["product_sku"], r["quantity"], r["unit_price"], r["currency"]) for r in rows],
        )
        staged = [
            dict(zip(("order_id", "product_sku", "quantity", "unit_price", "currency"), record))
            for record in connection.execute(
                "SELECT order_id, product_sku, quantity, unit_price, currency FROM raw_orders ORDER BY order_id"
            )
        ]
        mart_rows = _transform(staged, graph)
        connection.executemany("INSERT INTO sales_mart VALUES (?, ?, ?, ?, ?)", mart_rows)
    return {
        "raw_rows": len(rows),
        "mart_rows": len(mart_rows),
        "revenue_usd_cents": sum(row[2] for row in mart_rows),
        "graph_version": graph.version,
    }
