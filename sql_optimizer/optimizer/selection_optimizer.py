"""
Selects the best algorithm for each selection predicate on a table.
"""
from dataclasses import dataclass
from typing import Optional

from sql_optimizer.models.query import Condition
from sql_optimizer.models.schema import IndexType, Table
from sql_optimizer.optimizer import cost_estimator as ce
from sql_optimizer.optimizer import selectivity_estimator as se


@dataclass
class SelectionPlan:
    condition: str
    algorithm: str
    cost: int
    index_used: Optional[str]
    est_rows: int
    est_blocks: int


def best_selection_plan(table: Table, condition: Condition) -> SelectionPlan:
    """Choose the lowest-cost algorithm for a single selection predicate."""
    op = condition.operator.upper()
    if op in ("=", "LIKE"):
        return _equality_plan(table, condition)
    elif op in ("<", "<=", ">", ">="):
        return _range_plan(table, condition)
    else:
        return _linear_scan_plan(table, condition)


def best_combined_selection(table: Table, conditions: list[Condition]) -> list[SelectionPlan]:
    """Return one SelectionPlan per condition, sorted cheapest first."""
    plans = [best_selection_plan(table, c) for c in conditions]
    plans.sort(key=lambda p: p.cost)
    return plans


# ── internal ──────────────────────────────────────────────────────────────────

def _attr_name(ref: str) -> str:
    return ref.split(".")[-1]


def _equality_plan(table: Table, condition: Condition) -> SelectionPlan:
    attr = _attr_name(condition.left)
    attribute = table.get_attribute(attr)

    is_unique  = attribute.is_unique if attribute else False
    n_distinct = attribute.n_distinct if attribute else table.n_rows
    est_rows, est_blocks = se.estimate_equality_selection(
        table.n_rows, table.n_blocks, n_distinct, is_unique
    )

    indexes = table.get_indexes_for(attr)
    btree_clustering    = [i for i in indexes if i.index_type == IndexType.BTREE and i.is_clustering]
    btree_nonclustering = [i for i in indexes if i.index_type == IndexType.BTREE and not i.is_clustering]
    hash_indexes        = [i for i in indexes if i.index_type == IndexType.HASH]

    # ── Best available index access path (A2–A6) ──────────────────────────────
    idx_cost: Optional[int] = None
    idx_algo: Optional[str] = None
    if btree_clustering:
        h = btree_clustering[0].height or 3
        idx_cost = ce.cost_btree_equality_clustering(h, est_blocks)
        idx_algo = "B+ Tree (clustering) equality seek"
    elif hash_indexes:
        is_clust = hash_indexes[0].is_clustering
        idx_cost = ce.cost_hash_equality(is_clustering=is_clust, n_matching=est_rows)
        idx_algo = "Hash index equality lookup" if is_clust else "Hash index equality lookup (non-clustering)"
    elif btree_nonclustering:
        h = btree_nonclustering[0].height or 3
        idx_cost = ce.cost_btree_equality_nonclustering(h, est_rows)
        idx_algo = "B+ Tree (non-clustering) equality seek"

    # ── Linear scan (A1): br/2 if searching by a unique key, else br ───────────
    if is_unique:
        linear_cost = ce.cost_linear_scan_equality(table.n_blocks)
        linear_algo = "Linear scan (equality, unique attribute)"
    else:
        linear_cost = ce.cost_linear_scan(table.n_blocks)
        linear_algo = "Linear scan (equality)"

    # ── A7 ("Obrada upita", str. 8): choose the cheapest access path. A secondary
    #     index does one I/O per matching row, so a full scan can be cheaper. ──
    if idx_cost is not None and idx_cost <= linear_cost:
        return SelectionPlan(str(condition), idx_algo, idx_cost, attr, est_rows, est_blocks)
    return SelectionPlan(str(condition), linear_algo, linear_cost, None, est_rows, est_blocks)


def _range_plan(table: Table, condition: Condition) -> SelectionPlan:
    attr = _attr_name(condition.left)
    attribute = table.get_attribute(attr)
    n_distinct = attribute.n_distinct if attribute else None
    est_rows, est_blocks = se.estimate_range_selection(table.n_rows, table.n_blocks, n_distinct)

    indexes = table.get_indexes_for(attr)
    btree = [i for i in indexes if i.index_type == IndexType.BTREE]

    if btree:
        idx = btree[0]
        h = idx.height or 3
        if idx.is_clustering:
            # Cost = h (tree traversal) + estimated matching blocks (not br/2)
            idx_cost = h + est_blocks
            idx_algo = "B+ Tree (clustering) range scan"
        else:
            idx_cost = ce.cost_btree_range_nonclustering(h, est_rows)
            idx_algo = "B+ Tree (non-clustering) range scan"

        # A6 ("Obrada upita", str. 7): a secondary/index range scan does one I/O
        # per matching row, so a full linear scan can be cheaper. Pick the lower.
        linear_cost = ce.cost_linear_scan(table.n_blocks)
        if idx_cost <= linear_cost:
            return SelectionPlan(str(condition), idx_algo, idx_cost, attr, est_rows, est_blocks)
        return SelectionPlan(str(condition), "Linear scan (range, cheaper than index)",
                             linear_cost, None, est_rows, est_blocks)

    cost = ce.cost_linear_scan(table.n_blocks)
    return SelectionPlan(str(condition), "Linear scan (range)", cost, None, est_rows, est_blocks)


def _linear_scan_plan(table: Table, condition: Condition) -> SelectionPlan:
    cost = ce.cost_linear_scan(table.n_blocks)
    return SelectionPlan(str(condition), "Linear scan", cost, None, table.n_rows, table.n_blocks)
