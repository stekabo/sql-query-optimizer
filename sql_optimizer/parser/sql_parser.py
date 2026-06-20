import re
from typing import Optional

from sql_optimizer.models.query import Condition, ParsedQuery, TableRef


_OPERATORS = ["!=", "<=", ">=", "<>", "=", "<", ">", "LIKE"]


def parse_sql(query: str) -> ParsedQuery:
    """Parse a restricted SQL SELECT statement into a ParsedQuery."""
    q = _normalize(query)

    select_part = _extract_clause(q, "SELECT", ["FROM"])
    from_part   = _extract_clause(q, "FROM",   ["WHERE", "ORDER BY"])
    where_part  = _extract_clause(q, "WHERE",  ["ORDER BY"])
    order_part  = _extract_clause(q, "ORDER BY", [])

    from_tables = _parse_from(from_part)
    alias_map   = {t.ref.lower(): t.name for t in from_tables}
    all_refs    = {t.ref.lower() for t in from_tables}

    select_cols = _parse_select(select_part)
    conditions  = _parse_where(where_part, all_refs) if where_part else []
    order_by    = order_part.strip() if order_part else None

    # Resolve aliases in SELECT and ORDER BY back to table.attr form
    select_cols = [_resolve_ref(col, alias_map) for col in select_cols]
    if order_by:
        order_by = _resolve_ref(order_by, alias_map)

    # Resolve aliases in WHERE conditions
    for c in conditions:
        c.left  = _resolve_ref(c.left,  alias_map)
        c.right = _resolve_ref(c.right, alias_map) if c.is_join_condition else c.right

    return ParsedQuery(
        select=select_cols,
        from_tables=from_tables,
        where=conditions,
        order_by=order_by,
    )


# ── helpers ──────────────────────────────────────────────────────────────────

def _normalize(query: str) -> str:
    q = re.sub(r"\s+", " ", query.strip())
    q = re.sub(r"ORDER\s+BY", "ORDER BY", q, flags=re.IGNORECASE)
    return q


def _extract_clause(query: str, keyword: str, stop_keywords: list[str]) -> Optional[str]:
    pattern = re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
    m = pattern.search(query)
    if not m:
        return None

    start = m.end()
    end = len(query)
    for stop in stop_keywords:
        stop_re = re.compile(rf"\b{re.escape(stop)}\b", re.IGNORECASE)
        sm = stop_re.search(query, start)
        if sm and sm.start() < end:
            end = sm.start()

    return query[start:end].strip()


def _parse_select(text: Optional[str]) -> list[str]:
    if not text:
        return []
    return [col.strip() for col in text.split(",")]


def _parse_from(text: Optional[str]) -> list[TableRef]:
    """Parse 'TableA a, TableB b' or 'TableA, TableB' into TableRef list."""
    if not text:
        return []
    refs = []
    for part in text.split(","):
        tokens = part.strip().split()
        if len(tokens) == 1:
            refs.append(TableRef(name=tokens[0], alias=None))
        elif len(tokens) == 2:
            refs.append(TableRef(name=tokens[0], alias=tokens[1]))
        # ignore malformed entries
    return refs


def _parse_where(text: str, all_refs: set[str]) -> list[Condition]:
    raw_conditions = re.split(r"\bAND\b", text, flags=re.IGNORECASE)
    conditions = []
    for raw in raw_conditions:
        raw = raw.strip()
        if not raw:
            continue
        cond = _parse_condition(raw, all_refs)
        if cond:
            conditions.append(cond)
    return conditions


def _parse_condition(raw: str, all_refs: set[str]) -> Optional[Condition]:
    for op in _OPERATORS:
        pattern = re.compile(rf"(.+?)\s*{re.escape(op)}\s*(.+)", re.IGNORECASE)
        m = pattern.match(raw.strip())
        if m:
            left  = m.group(1).strip()
            right = m.group(2).strip()
            is_join = _is_attribute_ref(right, all_refs)
            return Condition(left=left, operator=op.upper(), right=right, is_join_condition=is_join)
    return None


def _is_attribute_ref(value: str, all_refs: set[str]) -> bool:
    if re.match(r"^'.*'$", value) or re.match(r"^\".*\"$", value):
        return False
    if re.match(r"^-?\d+(\.\d+)?$", value):
        return False
    if "." in value:
        qualifier = value.split(".")[0].lower()
        return qualifier in all_refs
    return bool(re.match(r"^[a-zA-Z_]\w*$", value))


def _resolve_ref(ref: str, alias_map: dict[str, str]) -> str:
    """Replace alias prefix with real table name: 's.ime' → 'Students.ime'."""
    if "." in ref:
        qualifier, attr = ref.split(".", 1)
        real_name = alias_map.get(qualifier.lower())
        if real_name:
            return f"{real_name}.{attr}"
    return ref
