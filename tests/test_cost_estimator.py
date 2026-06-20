import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sql_optimizer.optimizer.cost_estimator import (
    cost_block_nested_loop_join,
    cost_btree_equality_clustering,
    cost_btree_equality_nonclustering,
    cost_external_sort,
    cost_hash_equality,
    cost_hash_join,
    cost_linear_scan,
    cost_merge_join,
    cost_nested_loop_join,
    cost_projection,
)


def test_linear_scan():
    assert cost_linear_scan(100) == 100


def test_btree_clustering_equality():
    # height 3 → 3 + 1 = 4
    assert cost_btree_equality_clustering(3) == 4


def test_btree_nonclustering_equality():
    # height 3, 10 matching rows → 3 + 10 = 13
    assert cost_btree_equality_nonclustering(3, 10) == 13


def test_hash_equality():
    assert cost_hash_equality() == 1


def test_nested_loop_join():
    assert cost_nested_loop_join(10, 20) == 200


def test_block_nested_loop_join():
    # br=10, bs=100, B=10 → 10 + ceil(10/8)*100 = 10 + 2*100 = 210
    assert cost_block_nested_loop_join(10, 100, 10) == 210


def test_hash_join():
    assert cost_hash_join(10, 20) == 3 * (10 + 20)


def test_merge_join_both_sorted():
    assert cost_merge_join(10, 20, 5, r_sorted=True, s_sorted=True) == 30


def test_merge_join_unsorted_adds_sort_cost():
    sort_r = cost_external_sort(10, 5)
    sort_s = cost_external_sort(20, 5)
    expected = 10 + 20 + sort_r + sort_s
    assert cost_merge_join(10, 20, 5, r_sorted=False, s_sorted=False) == expected


def test_external_sort_fits_in_buffer():
    # br <= B → single pass → 2 * br
    assert cost_external_sort(5, 10) == 10


def test_external_sort_multiple_passes():
    # br=100, B=10 → runs=10, passes=ceil(log_9(10))=2 → 2*100*(1+2)=600
    result = cost_external_sort(100, 10)
    n_runs = math.ceil(100 / 10)
    passes = math.ceil(math.log(n_runs, 9))
    assert result == 2 * 100 * (1 + passes)


def test_projection():
    assert cost_projection(50) == 50
