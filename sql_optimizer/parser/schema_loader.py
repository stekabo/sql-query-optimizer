import json
from pathlib import Path

from sql_optimizer.models.schema import Attribute, Index, IndexType, Schema, Table


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

    tables = [_parse_table(t) for t in data["tables"]]
    return Schema(tables=tables)


def _parse_table(t: dict) -> Table:
    attributes = [_parse_attribute(a) for a in t["attributes"]]
    indexes = [_parse_index(i) for i in t.get("indexes", [])]
    return Table(
        name=t["name"],
        attributes=attributes,
        n_rows=t["n_rows"],
        n_blocks=t["n_blocks"],
        rows_per_block=t["rows_per_block"],
        indexes=indexes,
    )


def _parse_attribute(a: dict) -> Attribute:
    return Attribute(
        name=a["name"],
        attr_type=a["type"],
        is_unique=a.get("is_unique", False),
        n_distinct=a["n_distinct"],
    )


def _parse_index(i: dict) -> Index:
    raw_type = i["type"].lower()
    index_type = IndexType.BTREE if raw_type in ("btree", "b+tree", "b+ tree") else IndexType.HASH

    attrs = i["attributes"]
    if isinstance(attrs, str):
        attrs = [attrs]

    return Index(
        attributes=attrs,
        index_type=index_type,
        is_clustering=i.get("is_clustering", False),
        height=i.get("height", None),
    )
