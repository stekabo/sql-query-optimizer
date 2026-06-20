"""
Cardinality and size estimation formulas.

Assumes uniform value distribution (no histograms).
All estimates return (n_rows, n_blocks).
"""
import math


def estimate_equality_selection(
    n_rows: int, n_blocks: int, n_distinct: int, is_unique: bool
) -> tuple[int, int]:
    """
    Equality predicate selectivity: 1 / V(A, r).
    Unique attribute → exactly 1 row.
    """
    if is_unique:
        est_rows = 1
    else:
        est_rows = max(1, n_rows // n_distinct)
    blocking_factor = max(1, n_rows // n_blocks) if n_blocks > 0 else 1
    est_blocks = max(1, math.ceil(est_rows / blocking_factor))
    return est_rows, est_blocks


def estimate_range_selection(n_rows: int, n_blocks: int) -> tuple[int, int]:
    """
    Range predicate selectivity: assume 1/3 of rows match (no histogram).
    """
    est_rows = max(1, n_rows // 3)
    blocking_factor = max(1, n_rows // n_blocks) if n_blocks > 0 else 1
    est_blocks = max(1, math.ceil(est_rows / blocking_factor))
    return est_rows, est_blocks


def estimate_join(
    nr: int, br: int,
    ns: int, bs: int,
    v_join_r: int, v_join_s: int,
    row_size_r: int, row_size_s: int,
    block_size: int = 4096,
) -> tuple[int, int]:
    """
    Natural join size estimation.
    Formula: (nr * ns) / max(V(A, r), V(A, s))
    """
    denominator = max(v_join_r, v_join_s)
    est_rows = max(1, (nr * ns) // denominator)
    combined_row_size = max(1, row_size_r + row_size_s)
    est_blocking_factor = max(1, block_size // combined_row_size)
    est_blocks = max(1, math.ceil(est_rows / est_blocking_factor))
    return est_rows, est_blocks


def estimate_cross_product(
    nr: int, ns: int, row_size_r: int, row_size_s: int, block_size: int = 4096
) -> tuple[int, int]:
    est_rows = nr * ns
    combined_row_size = max(1, row_size_r + row_size_s)
    est_blocking_factor = max(1, block_size // combined_row_size)
    est_blocks = max(1, math.ceil(est_rows / est_blocking_factor))
    return est_rows, est_blocks
