from sql_optimizer.models.query import Condition, ParsedQuery, TableRef
from sql_optimizer.models.schema import Schema, Table


class SemanticError(Exception):
    pass


def check(query: ParsedQuery, schema: Schema) -> None:
    """Raise SemanticError if the parsed query is inconsistent with the schema."""
    tables = _resolve_tables(query.from_tables, schema)
    _check_select(query.select, tables)
    _check_where(query.where, tables)
    _check_order_by(query.order_by, tables)


def _resolve_tables(refs: list[TableRef], schema: Schema) -> dict[str, Table]:
    """Returns a dict keyed by real table name (lowercase) → Table."""
    resolved: dict[str, Table] = {}
    for ref in refs:
        t = schema.get_table(ref.name)
        if t is None:
            raise SemanticError(f"Table '{ref.name}' does not exist in the schema.")
        if ref.name.lower() in resolved:
            raise SemanticError(f"Table '{ref.name}' appears more than once in FROM clause.")
        resolved[ref.name.lower()] = t
    return resolved


def _resolve_attribute(ref: str, tables: dict[str, Table]) -> None:
    """Raise SemanticError if the attribute reference cannot be resolved.
    At this point aliases have already been replaced with real table names.
    """
    ref = ref.strip()

    if ref == "*":
        return

    if "." in ref:
        table_name, attr_name = ref.split(".", 1)
        t = tables.get(table_name.lower())
        if t is None:
            raise SemanticError(f"Table '{table_name}' in reference '{ref}' not in FROM clause.")
        if t.get_attribute(attr_name) is None:
            raise SemanticError(f"Attribute '{attr_name}' does not exist in table '{t.name}'.")
        return

    # Unqualified — must exist in exactly one table
    matches = [t for t in tables.values() if t.get_attribute(ref) is not None]
    if len(matches) == 0:
        raise SemanticError(f"Attribute '{ref}' not found in any table in the FROM clause.")
    if len(matches) > 1:
        table_names = ", ".join(m.name for m in matches)
        raise SemanticError(
            f"Ambiguous attribute '{ref}': exists in multiple tables ({table_names}). "
            "Use table.attribute notation."
        )


def _check_select(select_cols: list[str], tables: dict[str, Table]) -> None:
    for col in select_cols:
        _resolve_attribute(col, tables)


def _check_where(conditions: list[Condition], tables: dict[str, Table]) -> None:
    for cond in conditions:
        _resolve_attribute(cond.left, tables)
        if cond.is_join_condition:
            _resolve_attribute(cond.right, tables)


def _check_order_by(order_by: str | None, tables: dict[str, Table]) -> None:
    if order_by:
        _resolve_attribute(order_by, tables)
