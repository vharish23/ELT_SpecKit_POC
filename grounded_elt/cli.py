"""Run the grounded pipeline directly or via a DeepAgents operator."""

import argparse
import json
from pathlib import Path

from grounded_elt.clinical import run_clinical_pipeline
from grounded_elt.design_agents import run_design_agents, save_design
from grounded_elt.pipeline import KnowledgeGraph, run_pipeline
from grounded_elt.provenance import export_run_provenance


ROOT = Path(__file__).resolve().parent.parent


def build_agent(orders: Path, graph: Path, database: Path, model: str):
    from deepagents import create_deep_agent

    completed: list[dict[str, int | str]] = []
    inspected = False

    def inspect_knowledge_graph() -> str:
        """Read the versioned knowledge graph facts before running ELT."""
        nonlocal inspected
        kg = KnowledgeGraph(graph)
        inspected = True
        return json.dumps({"version": kg.version, "facts": [vars(fact) for fact in kg.facts]})

    def execute_grounded_elt() -> str:
        """Validate and load the fixed ELT pipeline; returns row counts and USD cents."""
        if not inspected:
            raise ValueError("Inspect the knowledge graph before executing ELT")
        if completed:
            raise ValueError("ELT has already executed for this agent run")
        result = run_pipeline(orders, graph, database)
        completed.append(result)
        return json.dumps(result)

    agent = create_deep_agent(
        model=model,
        tools=[inspect_knowledge_graph, execute_grounded_elt],
        system_prompt=(
            "You are an ELT operator. First call inspect_knowledge_graph to ground your "
            "understanding of SKU classifications and FX rates. Then call execute_grounded_elt "
            "exactly once. Never invent graph facts or produce executable SQL. Report only "
            "the result returned by the tool; do not use unrelated tools."
        ),
    )
    return agent, completed


def build_clinical_agent(data_dir: Path, graph: Path, database: Path, as_of: str, model: str):
    from deepagents import create_deep_agent

    completed: list[dict] = []
    inspected = False

    def inspect_clinical_knowledge_graph() -> str:
        """Inspect the synthetic code and facility mappings, not patient records."""
        nonlocal inspected
        kg = KnowledgeGraph(graph)
        inspected = True
        return json.dumps({"version": kg.version, "facts": [vars(fact) for fact in kg.facts]})

    def execute_clinical_etl() -> str:
        """Run fixed staging, encounter, facility LOS and readmission pipelines."""
        if not inspected:
            raise ValueError("Inspect the clinical graph before executing ETL")
        if completed:
            raise ValueError("Clinical ETL has already executed for this agent run")
        result = run_clinical_pipeline(data_dir, graph, database, as_of)
        completed.append(result)
        return json.dumps(result)

    agent = create_deep_agent(
        model=model,
        tools=[inspect_clinical_knowledge_graph, execute_clinical_etl],
        system_prompt=(
            "You operate a synthetic clinical quality ETL POC. First inspect_clinical_knowledge_graph, "
            "then execute_clinical_etl once. Report the returned aggregate values only. "
            "Never read patient records, invent mappings, execute SQL, or invoke unrelated tools."
        ),
    )
    return agent, completed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a synthetic graph-grounded ELT demonstration")
    parser.add_argument("--pipeline", choices=("clinical", "sales"), default="clinical")
    parser.add_argument("--orders", type=Path, default=ROOT / "data/orders.csv")
    parser.add_argument("--graph", type=Path, default=ROOT / "data/sales_knowledge_graph.json")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/clinical")
    parser.add_argument("--clinical-graph", type=Path, default=ROOT / "data/knowledge_graph.json")
    parser.add_argument("--as-of", default="2026-05-31T00:00:00", help="UTC data-observation cutoff")
    parser.add_argument("--database", type=Path, default=ROOT / "demo.sqlite")
    parser.add_argument("--export-run", help="Re-export an archived clinical run's PROV-O Turtle without rerunning ETL")
    parser.add_argument("--agent", action="store_true", help="Use a tool-calling DeepAgents model")
    parser.add_argument("--design-agents", action="store_true", help="Run mapping, SQL and quality agents (no SQL execution)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "design-output")
    parser.add_argument("--model", default="openai:gpt-4.1-mini", help="DeepAgents provider:model identifier")
    args = parser.parse_args()
    if args.export_run:
        if args.pipeline != "clinical" or args.agent or args.design_agents:
            parser.error("--export-run requires the clinical pipeline without agent modes")
        path = export_run_provenance(args.database, args.export_run)
        print(json.dumps({"run_id": args.export_run, "provenance_path": str(path)}, sort_keys=True))
        return
    if args.design_agents:
        if args.agent or args.pipeline != "clinical":
            parser.error("--design-agents requires the clinical pipeline and cannot be combined with --agent")
        result = run_design_agents(args.data_dir, args.clinical_graph, args.as_of, args.model)
        save_design(result, args.output_dir)
        print(json.dumps({"graph_version": result["mapping"]["graph_version"],
                          "quality_status": result["quality"]["status"],
                          "output_dir": str(args.output_dir)}, sort_keys=True))
        if result["quality"]["status"] != "PASS":
            raise SystemExit("Quality gate failed; see quality.json for failed checks")
        return
    if args.agent:
        if args.pipeline == "clinical":
            agent, completed = build_clinical_agent(
                args.data_dir, args.clinical_graph, args.database, args.as_of, args.model
            )
        else:
            agent, completed = build_agent(args.orders, args.graph, args.database, args.model)
        agent.invoke({"messages": [{"role": "user", "content": "Inspect graph evidence and run the sample ETL."}]})
        if not completed:
            raise RuntimeError("Agent did not execute the grounded ETL tool")
        result = completed[-1]
    elif args.pipeline == "clinical":
        result = run_clinical_pipeline(args.data_dir, args.clinical_graph, args.database, args.as_of)
    else:
        result = run_pipeline(args.orders, args.graph, args.database)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
