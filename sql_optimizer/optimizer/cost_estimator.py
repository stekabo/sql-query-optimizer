"""
I/O cost formulas from lecture material (BP2 - 5. Obrada upita).

All costs are expressed in number of block transfers.
Notation:
    br  — number of blocks in relation r
    nr  — number of rows in relation r
    fr  — blocking factor (rows per block)
    B   — buffer size in blocks
    h   — height of B+ tree index
"""
import math

from sql_optimizer.models.schema import Index, IndexType


# ── Selection ─────────────────────────────────────────────────────────────────

def cost_linear_scan(br: int) -> int:
    return br


def cost_linear_scan_equality(br: int) -> int:
    """Equality on non-unique attribute: average br/2 blocks read."""
    return br // 2


def cost_btree_equality_clustering(h: int, est_blocks: int = 1) -> int:
    """h levels traversal + est_blocks matching data blocks (consecutive, clustering)."""
    return h + est_blocks


def cost_btree_equality_nonclustering(h: int, n_matching: int) -> int:
    return h + n_matching


def cost_hash_equality(is_clustering: bool = True, n_matching: int = 1) -> int:
    """
    Hash index equality.
    Clustering: 1 block transfer (all matching tuples in same bucket block).
    Non-clustering: 1 (bucket) + n_matching (each tuple potentially on different block).
    """
    if is_clustering:
        return 1
    return 1 + n_matching


def cost_btree_range_clustering(h: int, est_blocks: int) -> int:
    """h levels traversal + estimated matching data blocks (consecutive, clustering)."""
    return h + est_blocks


def cost_btree_range_nonclustering(h: int, n_matching: int) -> int:
    """h levels traversal + n_matching rows (each potentially on a different block)."""
    return h + n_matching


# ── Join ──────────────────────────────────────────────────────────────────────

def cost_nested_loop_join(nr: int, bs: int, br: int) -> int:
    """nr * bs + br  (tuple-level NL, BP2-5 slide 18)."""
    return nr * bs + br


def cost_block_nested_loop_join(br: int, bs: int, B: int) -> int:
    """Cost = br + ceil(br / (B-2)) * bs. Requires B >= 3."""
    chunks = math.ceil(br / (B - 2))
    return br + chunks * bs


def cost_index_nested_loop_join(br: int, n_outer: int, index: Index, inner_nr: int, inner_br: int) -> int:
    """
    For each outer row, probe the index on the inner relation.
    Cost = br + nr * probe_cost
    """
    if index.index_type == IndexType.HASH:
        probe = 1
    else:
        h = index.height if index.height else 3
        if index.is_clustering:
            probe = h + 1
        else:
            probe = h + max(1, inner_nr // inner_br)
    return br + n_outer * probe


def cost_merge_join(br: int, bs: int, B: int, r_sorted: bool = False, s_sorted: bool = False) -> int:
    """Sort-merge join. Adds external sort cost for unsorted inputs."""
    base = br + bs
    if not r_sorted:
        base += cost_external_sort(br, B)
    if not s_sorted:
        base += cost_external_sort(bs, B)
    return base


def cost_hash_join(br: int, bs: int) -> int:
    """Classic two-phase hash join: 3 * (br + bs)."""
    return 3 * (br + bs)


# ── Sort ──────────────────────────────────────────────────────────────────────

def cost_external_sort(br: int, B: int) -> int:
    """
    External sort-merge.
    Cost = br * (2 * ceil(log_{B-1}(ceil(br/B))) + 1)
    Final pass writes are NOT counted (pipelining convention from Silberschatz).
    """
    if B < 2:
        raise ValueError("Buffer must have at least 2 blocks.")
    if br <= B:
        return br  # fits in memory: just read once, sort, stream output
    n_runs = math.ceil(br / B)
    passes = math.ceil(math.log(n_runs, B - 1))
    return br * (2 * passes + 1)


# ── Projection ────────────────────────────────────────────────────────────────

def cost_projection(br: int) -> int:
    """Projection without duplicate elimination: one full scan."""
    return br
