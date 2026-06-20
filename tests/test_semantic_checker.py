import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from sql_optimizer.parser.schema_loader import load_schema
from sql_optimizer.parser.sql_parser import parse_sql
from sql_optimizer.validator.semantic_checker import SemanticError, check

SCHEMA = {
    "tables": [
        {
            "name": "Students",
            "attributes": [
                {"name": "id",      "type": "int",        "is_unique": True,  "n_distinct": 1000},
                {"name": "name",    "type": "varchar(50)", "is_unique": False, "n_distinct": 950},
                {"name": "dept_id", "type": "int",        "is_unique": False, "n_distinct": 10},
            ],
            "n_rows": 1000, "n_blocks": 20, "rows_per_block": 50, "indexes": []
        },
        {
            "name": "Courses",
            "attributes": [
                {"name": "id",    "type": "int",        "is_unique": True,  "n_distinct": 100},
                {"name": "title", "type": "varchar(80)", "is_unique": False, "n_distinct": 100},
            ],
            "n_rows": 100, "n_blocks": 5, "rows_per_block": 20, "indexes": []
        }
    ]
}


def _check(sql):
    schema = load_schema(SCHEMA)
    q = parse_sql(sql)
    check(q, schema)


def test_valid_query():
    _check("SELECT Students.name FROM Students WHERE Students.dept_id = 2")


def test_nonexistent_table():
    with pytest.raises(SemanticError, match="does not exist"):
        _check("SELECT id FROM NonExistent")


def test_nonexistent_attribute():
    with pytest.raises(SemanticError, match="does not exist"):
        _check("SELECT Students.salary FROM Students")


def test_ambiguous_attribute():
    with pytest.raises(SemanticError, match="Ambiguous"):
        _check("SELECT id FROM Students, Courses")


def test_qualified_attribute_ok():
    _check("SELECT Students.id FROM Students, Courses")


def test_nonexistent_table_in_where():
    with pytest.raises(SemanticError):
        _check("SELECT Students.id FROM Students WHERE Ghost.x = 1")
