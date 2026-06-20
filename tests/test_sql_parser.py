import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sql_optimizer.parser.sql_parser import parse_sql


def test_simple_select():
    q = parse_sql("SELECT id, name FROM Students")
    assert q.select == ["id", "name"]
    assert q.table_names() == ["Students"]
    assert q.where == []
    assert q.order_by is None


def test_where_equality():
    q = parse_sql("SELECT id FROM Students WHERE Students.dept_id = 5")
    assert len(q.where) == 1
    c = q.where[0]
    assert c.left == "Students.dept_id"
    assert c.operator == "="
    assert c.right == "5"
    assert not c.is_join_condition


def test_join_condition_detected():
    q = parse_sql(
        "SELECT * FROM Students, Enrollments "
        "WHERE Students.id = Enrollments.student_id"
    )
    join_conds = q.join_conditions()
    assert len(join_conds) == 1
    assert join_conds[0].is_join_condition


def test_selection_vs_join_conditions():
    q = parse_sql(
        "SELECT * FROM Students, Enrollments "
        "WHERE Students.id = Enrollments.student_id AND Students.dept_id = 3"
    )
    assert len(q.join_conditions()) == 1
    assert len(q.selection_conditions()) == 1


def test_order_by():
    q = parse_sql("SELECT name FROM Students WHERE Students.id = 1 ORDER BY Students.name")
    assert q.order_by == "Students.name"


def test_range_condition():
    q = parse_sql("SELECT * FROM Students WHERE Students.gpa > 3.5")
    assert q.where[0].operator == ">"


def test_multiple_tables():
    q = parse_sql("SELECT * FROM A, B, C, D")
    assert len(q.from_tables) == 4


def test_alias_resolved_in_where():
    q = parse_sql(
        "SELECT s.name FROM Students s, Enrollments e "
        "WHERE s.id = e.student_id AND s.dept_id = 3"
    )
    # Aliases must be resolved to real table names
    assert q.table_names() == ["Students", "Enrollments"]
    join = q.join_conditions()
    assert len(join) == 1
    assert "Students." in join[0].left or "Enrollments." in join[0].left
    sel = q.selection_conditions()
    assert len(sel) == 1
    assert sel[0].left == "Students.dept_id"


def test_alias_resolved_in_select():
    q = parse_sql("SELECT s.name, s.gpa FROM Students s")
    assert "Students.name" in q.select
    assert "Students.gpa" in q.select


def test_alias_resolved_in_order_by():
    q = parse_sql(
        "SELECT s.name FROM Students s, Enrollments e "
        "WHERE s.id = e.student_id ORDER BY e.grade"
    )
    assert q.order_by == "Enrollments.grade"
