"""
Assembles the complete evaluation plan from parsed query + schema.
"""
from typing import Optional

from sql_optimizer.models.plan import EvaluationPlan, OperationStep
from sql_optimizer.models.query import Condition, ParsedQuery
from sql_optimizer.models.schema import IntermediateResult, Schema, Table
from sql_optimizer.optimizer import cost_estimator as ce
from sql_optimizer.optimizer.join_optimizer import JoinOrder, JoinPlan, greedy_join_order
from sql_optimizer.optimizer.selection_optimizer import SelectionPlan, best_combined_selection


def build_plan(query: ParsedQuery, schema: Schema, buffer_size: int) -> EvaluationPlan:
    plan = EvaluationPlan(query=_format_query(query))

    # ── 1. Selection on each table ────────────────────────────────────────────
    tables = [schema.get_table(t) for t in query.table_names()]
    selection_conditions = query.selection_conditions()

    intermediates: list[IntermediateResult] = []
    for table in tables:
        table_conditions = _conditions_for_table(table, selection_conditions)

        if table_conditions:
            sel_plans = best_combined_selection(table, table_conditions)
            for sp in sel_plans:
                step = OperationStep(
                    description=f"Selection on {table.name}: {sp.condition}",
                    algorithm=sp.algorithm,
                    cost=sp.cost,
                    details=_selection_details(sp, table),
                )
                plan.steps.append(step)
            # Use the most selective plan's output as the intermediate
            best_sp = min(sel_plans, key=lambda p: p.est_rows)
            inter = IntermediateResult(
                label=table.name,
                n_rows=best_sp.est_rows,
                n_blocks=best_sp.est_blocks,
                attributes=[f"{table.name}.{a.name}" for a in table.attributes],
                row_size=table.row_size(),
                buffer_size=buffer_size,
            )
        else:
            # No selection — full table
            inter = IntermediateResult.from_table(table, buffer_size)

        intermediates.append(inter)

    # ── 2. Join(s) ────────────────────────────────────────────────────────────
    join_conditions = query.join_conditions()
    current_result: Optional[IntermediateResult] = None

    if len(tables) == 1:
        current_result = intermediates[0]
    else:
        join_order: JoinOrder = greedy_join_order(
            tables=tables,
            join_conditions=join_conditions,
            schema=schema,
            buffer_size=buffer_size,
            base_intermediates=intermediates,
        )
        for jp in join_order.steps:
            conds = _find_join_conditions_for_step(jp, join_conditions)
            if conds:
                join_type = "Conjunctive Join" if len(conds) > 1 else "Join"
                cond_str = " ON " + " AND ".join(str(c) for c in conds)
            else:
                join_type = "Cross Product"
                cond_str = ""
            step = OperationStep(
                description=f"{join_type}: {jp.left_label} JOIN {jp.right_label}{cond_str}",
                algorithm=jp.algorithm,
                cost=jp.cost,
                details=_join_details(jp, buffer_size),
            )
            plan.steps.append(step)
        current_result = join_order.steps[-1].result

    # ── 3. Projection ─────────────────────────────────────────────────────────
    if query.select != ["*"]:
        proj_cost = ce.cost_projection(current_result.n_blocks)
        plan.steps.append(OperationStep(
            description=f"Projection: {', '.join(query.select)}",
            algorithm="Column elimination (no duplicate removal)",
            cost=proj_cost,
            details=f"Reads {current_result.n_blocks} blocks, eliminates unreferenced columns.",
        ))

    # ── 4. ORDER BY ───────────────────────────────────────────────────────────
    if query.order_by:
        order_attr = query.order_by.split(".")[-1]
        # Check if a clustering index already provides the order
        sort_table = _find_table_for_attr(order_attr, tables)
        index_provides_order = False
        if sort_table:
            for idx in sort_table.get_indexes_for(order_attr):
                if idx.is_clustering:
                    index_provides_order = True
                    break

        if index_provides_order:
            plan.steps.append(OperationStep(
                description=f"ORDER BY {query.order_by}",
                algorithm="Clustering index provides sorted order — no sort needed",
                cost=0,
                details="Result already sorted by clustering index on the ORDER BY attribute.",
            ))
        else:
            sort_cost = ce.cost_external_sort(current_result.n_blocks, buffer_size)
            plan.steps.append(OperationStep(
                description=f"ORDER BY {query.order_by}",
                algorithm="External sort-merge",
                cost=sort_cost,
                details=f"Sorting {current_result.n_blocks} blocks with buffer B={buffer_size}.",
            ))

    plan.final_result = current_result
    return plan


# ── helpers ───────────────────────────────────────────────────────────────────

def _conditions_for_table(table: Table, conditions: list[Condition]) -> list[Condition]:
    result = []
    for c in conditions:
        attr = c.left.split(".")[-1]
        tbl  = c.left.split(".")[0] if "." in c.left else None
        if tbl and tbl.lower() != table.name.lower():
            continue
        if table.get_attribute(attr):
            result.append(c)
    return result


def _find_join_conditions_for_step(jp: JoinPlan, conditions: list[Condition]) -> list[Condition]:
    """Return ALL conditions connecting the left and right sides of a join (conjunctive join support)."""
    left_tables  = {t.lower().strip("() ") for t in jp.left_label.replace("JOIN", ",").split(",")}
    right_tables = {t.lower().strip("() ") for t in jp.right_label.replace("JOIN", ",").split(",")}
    matched = []
    for c in conditions:
        lt = (c.left.split(".")[0] if "." in c.left else "").lower()
        rt = (c.right.split(".")[0] if "." in c.right else "").lower()
        if (lt in left_tables and rt in right_tables) or (rt in left_tables and lt in right_tables):
            matched.append(c)
    return matched


def _find_table_for_attr(attr: str, tables: list[Table]) -> Optional[Table]:
    for t in tables:
        if t.get_attribute(attr):
            return t
    return None


def _selection_details(sp: SelectionPlan, table: Table) -> str:
    idx_info = f", index on '{sp.index_used}'" if sp.index_used else ""
    return (
        f"Table: {table.name} (br={table.n_blocks}, nr={table.n_rows}){idx_info}. "
        f"Estimated output: ~{sp.est_rows} rows, ~{sp.est_blocks} blocks."
    )


def _join_details(jp: JoinPlan, buffer_size: int) -> str:
    idx_info = f", index on '{jp.index_used}'" if jp.index_used else ""
    return (
        f"Left: {jp.left_label} ({jp.result.n_blocks} blk est.), "
        f"Right: {jp.right_label}{idx_info}, B={buffer_size}. "
        f"Estimated output: ~{jp.result.n_rows} rows, ~{jp.result.n_blocks} blocks."
    )


def _format_query(q: ParsedQuery) -> str:
    parts = [
        f"SELECT {', '.join(q.select)}",
        f"FROM {', '.join(t.name + (' ' + t.alias if t.alias else '') for t in q.from_tables)}",
    ]
    if q.where:
        parts.append(f"WHERE {' AND '.join(str(c) for c in q.where)}")
    if q.order_by:
        parts.append(f"ORDER BY {q.order_by}")
    return " ".join(parts)
