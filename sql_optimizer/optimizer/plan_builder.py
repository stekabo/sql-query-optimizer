"""
Assembles the complete evaluation plan from parsed query + schema.
"""
import math
from typing import Optional

from sql_optimizer.models.plan import EvaluationPlan, OperationStep
from sql_optimizer.models.query import Condition, ParsedQuery
from sql_optimizer.models.schema import IntermediateResult, Schema, Table
from sql_optimizer.optimizer import cost_estimator as ce
from sql_optimizer.optimizer.join_optimizer import JoinOrder, JoinPlan, greedy_join_order
from sql_optimizer.optimizer.selection_optimizer import SelectionPlan, best_combined_selection


def build_plan(query: ParsedQuery, schema: Schema, buffer_size: int) -> EvaluationPlan:
    plan = EvaluationPlan(query=_format_query(query))

    # ── 1. Selection on each table (compute now, emit after join order) ────────
    tables = [schema.get_table(t) for t in query.table_names()]
    selection_conditions = query.selection_conditions()
    join_conditions = query.join_conditions()

    intermediates: list[IntermediateResult] = []
    table_sel_plans: list[Optional[list[SelectionPlan]]] = []
    for table in tables:
        table_conditions = _conditions_for_table(table, selection_conditions)

        if table_conditions:
            sel_plans = best_combined_selection(table, table_conditions)
            # Output cardinality: conjunction of all predicates' selectivities
            # ("Optimizacija upita", str. 26).
            est_rows, est_blocks = _conjunctive_output(table, sel_plans)
            inter = IntermediateResult(
                label=table.name,
                n_rows=est_rows,
                n_blocks=est_blocks,
                attributes=[f"{table.name}.{a.name}" for a in table.attributes],
                row_size=table.row_size(),
                buffer_size=buffer_size,
            )
            table_sel_plans.append(sel_plans)
        else:
            # No selection — full table
            inter = IntermediateResult.from_table(table, buffer_size)
            table_sel_plans.append(None)

        intermediates.append(inter)

    # ── 2. Determine join order early, so selection steps know which tables are
    # the *indexed inner* of an index nested-loop join. A selection on such a
    # relation that cannot use an index for the join is applied as a residual
    # DURING the index probe — pipelined, cost 0 ("Optimizacija upita", str. 34,
    # Plan evaluacije 2: "Cena selekcije: Pipelining, te je cena 0").
    in_memory = False
    join_order: Optional[JoinOrder] = None
    do_join = len(tables) > 1 and not _fits_in_memory(intermediates, buffer_size)
    if do_join:
        join_order = greedy_join_order(
            tables=tables,
            join_conditions=join_conditions,
            schema=schema,
            buffer_size=buffer_size,
            base_intermediates=intermediates,
        )
    inl_inner_tables = _inl_inner_tables(join_order) if join_order else set()

    # ── 3. Emit selection steps (order preserved: selections before joins) ─────
    for table, sel_plans in zip(tables, table_sel_plans):
        if sel_plans is None:
            continue
        cheapest = sel_plans[0]  # best_combined_selection sorts cheapest first
        # Residual-in-INL rule: relation is the indexed inner of an INL and its
        # cheapest access path is a linear scan (no index for this predicate) →
        # the predicate is a pipelined residual on the rows fetched by the join.
        pipelined_residual = (
            table.name.lower() in inl_inner_tables and cheapest.index_used is None
        )
        if pipelined_residual:
            plan.steps.append(OperationStep(
                description=f"Selection on {table.name}: {cheapest.condition}",
                algorithm="Residual filter during index nested-loop join (pipelined)",
                cost=0,
                details=(
                    "Applied on rows fetched by the index nested-loop probe — "
                    "pipelined, no separate scan (str. 34, Plan evaluacije 2)."
                ),
            ))
        else:
            # A7 — conjunctive selection using one index ("Obrada upita", str. 8):
            # apply only the single cheapest access path; the remaining predicates
            # are tested in memory on the already-fetched rows, adding no extra I/O.
            plan.steps.append(OperationStep(
                description=f"Selection on {table.name}: {cheapest.condition}",
                algorithm=cheapest.algorithm,
                cost=cheapest.cost,
                details=_selection_details(cheapest, table),
            ))
        for sp in sel_plans[1:]:
            plan.steps.append(OperationStep(
                description=f"Selection on {table.name}: {sp.condition}",
                algorithm="In-memory filter on already-fetched rows (conjunctive selection)",
                cost=0,
                details="Tested in memory on rows fetched by the cheapest access path — no additional I/O.",
            ))

    # ── 4. Join(s) ────────────────────────────────────────────────────────────
    current_result: Optional[IntermediateResult] = None

    if len(tables) == 1:
        current_result = intermediates[0]
    elif not do_join:
        # All selected relations fit in the buffer — join and sort are free
        total_blocks = sum(i.n_blocks for i in intermediates)
        plan.steps.append(OperationStep(
            description=f"Join: {' JOIN '.join(i.label for i in intermediates)}",
            algorithm="In-memory join (all relations fit in buffer)",
            cost=0,
            details=(
                f"Total blocks after selection: {total_blocks} <= B={buffer_size}. "
                "All relations loaded into memory — no additional I/O needed."
            ),
        ))
        in_memory = True
        current_result = _merge_intermediates(intermediates, join_conditions, schema, buffer_size)
    else:
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
    # Projection (without duplicate elimination) is a non-blocking, pipelined
    # operation: it streams tuples and eliminates columns on the fly, so it adds
    # no I/O. "Optimizacija upita", str. 33-34: "projekcija: Pipelining, cena 0".
    if query.select != ["*"]:
        plan.steps.append(OperationStep(
            description=f"Projection: {', '.join(query.select)}",
            algorithm="Column elimination (pipelined, no duplicate removal)",
            cost=0,
            details="Pipelined into the preceding operation — no extra I/O (str. 33-34).",
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

        if in_memory:
            plan.steps.append(OperationStep(
                description=f"ORDER BY {query.order_by}",
                algorithm="In-memory sort (all data already in buffer)",
                cost=0,
                details="All data fits in buffer — sort requires no disk I/O.",
            ))
        elif index_provides_order:
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

def _inl_inner_tables(join_order: JoinOrder) -> set[str]:
    """
    Names (lowercase) of tables used as the *indexed inner* relation of an index
    nested-loop join. The join probes such a table via its base-table index, so
    a non-index selection on it is applied as a pipelined residual (str. 34).
    """
    inners: set[str] = set()
    for jp in join_order.steps:
        if jp.index_used and "Index Nested Loop" in jp.algorithm:
            inners.add(jp.index_used.split(".")[0].lower())
    return inners


def _conjunctive_output(table: Table, sel_plans: list[SelectionPlan]) -> tuple[int, int]:
    """
    Estimate the output size of a (possibly conjunctive) selection.

    Single predicate: identical to the single-condition estimate.
    Multiple predicates: conjunction of selectivities — nr * Π(si/nr)
    ("Optimizacija upita", str. 26).
    """
    if len(sel_plans) == 1:
        sp = sel_plans[0]
        return sp.est_rows, sp.est_blocks

    n_rows = table.n_rows
    if n_rows <= 0:
        return 1, 1

    rows = float(n_rows)
    for sp in sel_plans:
        rows *= sp.est_rows / n_rows
    est_rows = max(1, round(rows))

    blocking_factor = max(1, n_rows // table.n_blocks) if table.n_blocks > 0 else 1
    est_blocks = max(1, math.ceil(est_rows / blocking_factor))
    return est_rows, est_blocks


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
        f"Left: {jp.left_label} ({jp.left_n_blocks} blk), "
        f"Right: {jp.right_label} ({jp.right_n_blocks} blk){idx_info}, B={buffer_size}. "
        f"Estimated output: ~{jp.result.n_rows} rows, ~{jp.result.n_blocks} blocks."
    )


def _fits_in_memory(intermediates: list[IntermediateResult], buffer_size: int) -> bool:
    """True if all intermediate results together fit in the buffer (leaving 2 blocks for I/O)."""
    return sum(i.n_blocks for i in intermediates) <= buffer_size - 2


def _merge_intermediates(
    intermediates: list[IntermediateResult],
    join_conditions: list[Condition],
    schema: Schema,
    buffer_size: int,
) -> IntermediateResult:
    """Estimate the result of joining all intermediates (for in-memory case)."""
    result = intermediates[0]
    for other in intermediates[1:]:
        cond = _find_join_conditions_for_step(
            type("JP", (), {"left_label": result.label, "right_label": other.label})(),
            join_conditions
        )
        from sql_optimizer.optimizer import selectivity_estimator as se
        if cond:
            left_attr  = cond[0].left.split(".")[-1]
            right_attr = cond[0].right.split(".")[-1]
            v_left  = next((a.n_distinct for t in schema.tables for a in t.attributes if a.name.lower() == left_attr.lower()), result.n_rows)
            v_right = next((a.n_distinct for t in schema.tables for a in t.attributes if a.name.lower() == right_attr.lower()), other.n_rows)
            est_rows, est_blocks = se.estimate_join(
                result.n_rows, result.n_blocks,
                other.n_rows, other.n_blocks,
                v_left, v_right,
                result.row_size, other.row_size,
            )
        else:
            est_rows, est_blocks = se.estimate_cross_product(
                result.n_rows, other.n_rows, result.row_size, other.row_size
            )
        result = IntermediateResult(
            label=f"({result.label} JOIN {other.label})",
            n_rows=est_rows,
            n_blocks=est_blocks,
            attributes=result.attributes + other.attributes,
            row_size=result.row_size + other.row_size,
            buffer_size=buffer_size,
        )
    return result


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
