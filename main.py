"""
SQL Query Optimizer — entry point.

Usage:
    python main.py --schema data/example_schema.json --buffer 10
    (SQL query is entered interactively)

Or pass everything via arguments:
    python main.py --schema data/example_schema.json --buffer 10 \
        --query "SELECT Students.name FROM Students, Enrollments WHERE Students.id = Enrollments.student_id AND Students.dept_id = 5"
"""
import argparse
import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent))

from sql_optimizer.optimizer.plan_builder import build_plan
from sql_optimizer.output.plan_printer import print_plan
from sql_optimizer.parser.schema_loader import load_schema
from sql_optimizer.parser.sql_parser import parse_sql
from sql_optimizer.validator.semantic_checker import SemanticError, check


def main() -> None:
    args = _parse_args()

    # ── Load schema ───────────────────────────────────────────────────────────
    schema_path = Path(args.schema)
    if not schema_path.exists():
        _die(f"Schema file not found: {schema_path}")
    schema = load_schema(schema_path)
    print(f"[OK] Schema loaded: {len(schema.tables)} table(s) — "
          f"{', '.join(t.name for t in schema.tables)}")

    # ── Get SQL query ─────────────────────────────────────────────────────────
    if args.query:
        sql = args.query.strip()
    else:
        print("\nEnter SQL query (single line):")
        sql = input("> ").strip()

    if not sql:
        _die("Empty query.")

    # ── Parse ─────────────────────────────────────────────────────────────────
    try:
        parsed = parse_sql(sql)
    except Exception as e:
        _die(f"Parse error: {e}")

    print(f"[OK] Parsed: SELECT from {parsed.table_names()}, "
          f"{len(parsed.where)} condition(s)")

    # ── Validate ──────────────────────────────────────────────────────────────
    try:
        check(parsed, schema)
    except SemanticError as e:
        _die(f"Semantic error: {e}")

    print("[OK] Semantic check passed.")

    # ── Optimize and build plan ───────────────────────────────────────────────
    plan = build_plan(parsed, schema, buffer_size=args.buffer)

    # ── Print plan ────────────────────────────────────────────────────────────
    print_plan(plan)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SQL Query Cost Optimizer")
    p.add_argument("--schema",  required=True,       help="Path to JSON schema file")
    p.add_argument("--buffer",  type=int, default=10, help="Buffer size in blocks (default: 10)")
    p.add_argument("--query",   default=None,         help="SQL query string (optional; prompted if omitted)")
    return p.parse_args()


def _die(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
