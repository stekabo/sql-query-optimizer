"""
Selects the best join algorithm for each pair and determines the join order
using a greedy heuristic (always join the pair with the smallest result next).
"""
from dataclasses import dataclass, field
from typing import Optional

from sql_optimizer.models.query import Condition
from sql_optimizer.models.schema import Index, IndexType, IntermediateResult, Schema, Table
from sql_optimizer.optimizer import cost_estimator as ce
from sql_optimizer.optimizer import selectivity_estimator as se


@dataclass
class JoinPlan:
    left_label: str
    right_label: str
    algorithm: str
    cost: int
    index_used: Optional[str]
    result: IntermediateResult        # estimated output statistics


@dataclass
class JoinOrder:
    steps: list[JoinPlan] = field(default_factory=list)

    @property
    def total_cost(self) -> int:
        return sum(s.cost for s in self.steps)


def best_join_plan(
    left: IntermediateResult,
    right: IntermediateResult,
    join_condition: Optional[Condition],
    schema: Schema,
    buffer_size: int,
) -> JoinPlan:
    """Choose the lowest-cost join algorithm for a pair of relations."""
    candidates: list[JoinPlan] = []

    # ── NL join ──────────────────────────────────────────────────────────────
    nl_cost = ce.cost_nested_loop_join(left.n_blocks, right.n_blocks)
    candidates.append(_make_plan(left, right, "Nested Loop Join", nl_cost, None, join_condition, schema, buffer_size))

    # ── Block NL join ─────────────────────────────────────────────────────────
    bnl_cost = ce.cost_block_nested_loop_join(left.n_blocks, right.n_blocks, buffer_size)
    candidates.append(_make_plan(left, right, "Block Nested Loop Join", bnl_cost, None, join_condition, schema, buffer_size))

    # ── Indexed NL join ───────────────────────────────────────────────────────
    if join_condition:
        inner_table, inner_attr = _find_inner_table_and_attr(right.label, join_condition, schema)
        if inner_table and inner_attr:
            indexes = inner_table.get_indexes_for(inner_attr)
            if indexes:
                idx = _best_index(indexes)
                inl_cost = ce.cost_index_nested_loop_join(
                    left.n_blocks, left.n_rows, idx,
                    inner_table.n_rows, inner_table.n_blocks
                )
                candidates.append(_make_plan(
                    left, right, "Index Nested Loop Join", inl_cost,
                    f"{inner_table.name}.{inner_attr}", join_condition, schema, buffer_size
                ))

    # ── Merge join ────────────────────────────────────────────────────────────
    merge_cost = ce.cost_merge_join(left.n_blocks, right.n_blocks, buffer_size)
    candidates.append(_make_plan(left, right, "Sort-Merge Join", merge_cost, None, join_condition, schema, buffer_size))

    # ── Hash join ─────────────────────────────────────────────────────────────
    hash_cost = ce.cost_hash_join(left.n_blocks, right.n_blocks)
    candidates.append(_make_plan(left, right, "Hash Join", hash_cost, None, join_condition, schema, buffer_size))

    candidates.sort(key=lambda p: p.cost)
    return candidates[0]


def greedy_join_order(
    tables: list[Table],
    join_conditions: list[Condition],
    schema: Schema,
    buffer_size: int,
    base_intermediates: Optional[list[IntermediateResult]] = None,
) -> JoinOrder:
    """
    Greedy join ordering: at each step pick the pair whose join produces
    the smallest intermediate result.

    base_intermediates: pre-computed intermediates (e.g. after selections).
    If None, initialises from raw table statistics.
    """
    if base_intermediates is not None:
        remaining: list[IntermediateResult] = list(base_intermediates)
    else:
        remaining = [IntermediateResult.from_table(t, buffer_size) for t in tables]

    order = JoinOrder()

    while len(remaining) > 1:
        best_plan: Optional[JoinPlan] = None
        best_i, best_j = 0, 1

        for i in range(len(remaining)):
            for j in range(i + 1, len(remaining)):
                left  = remaining[i]
                right = remaining[j]
                cond  = _find_join_condition(left.label, right.label, join_conditions)
                plan  = best_join_plan(left, right, cond, schema, buffer_size)
                if best_plan is None or plan.result.n_rows < best_plan.result.n_rows:
                    best_plan = plan
                    best_i, best_j = i, j

        order.steps.append(best_plan)
        # Replace the two joined relations with their result
        new_remaining = [r for k, r in enumerate(remaining) if k not in (best_i, best_j)]
        new_remaining.append(best_plan.result)
        remaining = new_remaining

    return order


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_plan(
    left: IntermediateResult,
    right: IntermediateResult,
    algorithm: str,
    cost: int,
    index_used: Optional[str],
    join_condition: Optional[Condition],
    schema: Schema,
    buffer_size: int,
) -> JoinPlan:
    result = _estimate_join_result(left, right, join_condition, schema, buffer_size)
    return JoinPlan(
        left_label=left.label,
        right_label=right.label,
        algorithm=algorithm,
        cost=cost,
        index_used=index_used,
        result=result,
    )


def _estimate_join_result(
    left: IntermediateResult,
    right: IntermediateResult,
    condition: Optional[Condition],
    schema: Schema,
    buffer_size: int,
) -> IntermediateResult:
    if condition is None:
        est_rows, est_blocks = se.estimate_cross_product(
            left.n_rows, right.n_rows, left.row_size, right.row_size
        )
    else:
        left_attr  = _attr_name(condition.left)
        right_attr = _attr_name(condition.right)
        v_left  = _n_distinct(left_attr,  left.label,  schema) or left.n_rows
        v_right = _n_distinct(right_attr, right.label, schema) or right.n_rows
        est_rows, est_blocks = se.estimate_join(
            left.n_rows, left.n_blocks,
            right.n_rows, right.n_blocks,
            v_left, v_right,
            left.row_size, right.row_size,
        )

    combined_attrs = left.attributes + right.attributes
    label = f"({left.label} JOIN {right.label})"
    return IntermediateResult(
        label=label,
        n_rows=est_rows,
        n_blocks=est_blocks,
        attributes=combined_attrs,
        row_size=left.row_size + right.row_size,
        buffer_size=buffer_size,
    )


def _find_join_condition(
    left_label: str, right_label: str, conditions: list[Condition]
) -> Optional[Condition]:
    """Find a join condition that connects the two relations."""
    left_tables  = {t.lower().strip("()") for t in left_label.replace("JOIN", ",").split(",") if t.strip()}
    right_tables = {t.lower().strip("()") for t in right_label.replace("JOIN", ",").split(",") if t.strip()}

    for c in conditions:
        l_tbl = _table_name(c.left).lower()
        r_tbl = _table_name(c.right).lower()
        if (l_tbl in left_tables and r_tbl in right_tables) or \
           (r_tbl in left_tables and l_tbl in right_tables):
            return c
    return None


def _find_inner_table_and_attr(
    right_label: str, condition: Condition, schema: Schema
) -> tuple[Optional[Table], Optional[str]]:
    """Find which side of the condition is on the inner (right) relation."""
    right_tables = {t.lower().strip() for t in right_label.replace("JOIN", ",").split(",") if t.strip()}

    for ref, other_ref in [(condition.left, condition.right), (condition.right, condition.left)]:
        tbl = _table_name(ref).lower()
        if tbl in right_tables:
            table = schema.get_table(tbl)
            attr  = _attr_name(ref)
            return table, attr
    return None, None


def _best_index(indexes: list[Index]) -> Index:
    """Prefer clustering B+ tree, then hash, then non-clustering B+ tree."""
    for idx in indexes:
        if idx.index_type == IndexType.BTREE and idx.is_clustering:
            return idx
    for idx in indexes:
        if idx.index_type == IndexType.HASH:
            return idx
    return indexes[0]


def _n_distinct(attr: str, label: str, schema: Schema) -> Optional[int]:
    """Look up V(A,r) for an attribute from a base table."""
    for t in schema.tables:
        if t.name.lower() in label.lower():
            a = t.get_attribute(attr)
            if a:
                return a.n_distinct
    return None


def _attr_name(ref: str) -> str:
    return ref.split(".")[-1]


def _table_name(ref: str) -> str:
    parts = ref.split(".")
    return parts[0] if len(parts) > 1 else ""
