import json
from pathlib import Path

from sql_optimizer.models.schema import Attribute, Index, IndexType, Schema, Table

_MISSING = object()


def load_schema(source: str | Path | dict) -> Schema:
    """Load a Schema from a JSON file path, JSON string, or already-parsed dict."""
    if isinstance(source, dict):
        data = source
    elif isinstance(source, (str, Path)):
        path = Path(source)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = json.loads(source)
    else:
        raise TypeError(f"Unsupported source type: {type(source)}")

    # Two supported layouts:
    #   1. Flat:   { "tables": [...] }
    #   2. Nested: { "bufferBlocks": N, "schema": { "tables": [...] } }
    if "tables" not in data and isinstance(data.get("schema"), dict):
        data = data["schema"]

    if "tables" not in data:
        raise KeyError(
            "Schema JSON must contain a 'tables' array "
            "(optionally nested under a 'schema' object)."
        )

    tables = [_parse_table(t) for t in data["tables"]]
    return Schema(tables=tables)


def _pick(d: dict, *keys, default=_MISSING):
    """Return the first present key's value; supports both naming conventions."""
    for k in keys:
        if k in d:
            return d[k]
    if default is _MISSING:
        raise KeyError(f"None of {keys} found in object with keys {list(d.keys())}.")
    return default


def _parse_table(t: dict) -> Table:
    attributes = [_parse_attribute(a) for a in t["attributes"]]
    indexes = [_parse_index(i) for i in t.get("indexes", [])]
    return Table(
        name=t["name"],
        attributes=attributes,
        n_rows=_pick(t, "n_rows", "rowCount"),
        n_blocks=_pick(t, "n_blocks", "blockCount"),
        rows_per_block=_pick(t, "rows_per_block", "rowsPerBlock"),
        indexes=indexes,
    )


def _parse_attribute(a: dict) -> Attribute:
    return Attribute(
        name=a["name"],
        attr_type=a["type"],
        is_unique=_pick(a, "is_unique", "unique", default=False),
        n_distinct=_pick(a, "n_distinct", "distinctValues"),
    )


def _parse_index(i: dict) -> Index:
    raw_type = i["type"].lower()
    index_type = IndexType.HASH if "hash" in raw_type else IndexType.BTREE

    attrs = i["attributes"]
    if isinstance(attrs, str):
        attrs = [attrs]

    return Index(
        attributes=attrs,
        index_type=index_type,
        is_clustering=_pick(i, "is_clustering", "clustered", default=False),
        height=_pick(i, "height", "treeHeight", default=None),
    )
