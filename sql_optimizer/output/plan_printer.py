import sys

from sql_optimizer.models.plan import EvaluationPlan

# Force UTF-8 output on Windows so box-drawing characters render correctly
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def print_plan(plan: EvaluationPlan) -> None:
    """Print the evaluation plan to stdout in a structured, readable format."""
    width = 72
    line  = "-" * width

    print()
    print("=" * (width + 2))
    print((" SQL QUERY OPTIMIZER -- EVALUATION PLAN").center(width + 2))
    print("=" * (width + 2))
    print()
    print("  Query:")
    print(f"  {plan.query}")
    print()
    print(line)

    for i, step in enumerate(plan.steps, start=1):
        print(f"  Step {i}: {step.description}")
        print(f"    Algorithm : {step.algorithm}")
        if step.cost > 0:
            print(f"    Cost      : {step.cost} block transfer(s)")
        elif step.cost == 0:
            print(f"    Cost      : 0 (free — no I/O needed)")
        if step.details:
            print(f"    Details   : {step.details}")
        print()

    print(line)
    print(f"  TOTAL COST: {plan.total_cost} block transfer(s)")
    print(line)

    if plan.final_result:
        fr = plan.final_result
        print(f"  Final result : ~{fr.n_rows} rows, ~{fr.n_blocks} blocks  [{fr.label}]")
    print()


def format_plan(plan: EvaluationPlan) -> str:
    """Return the plan as a formatted string (useful for testing/logging)."""
    import io, sys
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        print_plan(plan)
    finally:
        sys.stdout = old_stdout
    return buf.getvalue()
