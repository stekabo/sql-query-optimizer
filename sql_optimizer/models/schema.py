from dataclasses import dataclass, field
from enum import Enum
from typing import Optional



class IndexType(Enum):
    BTREE = "btree"
    HASH = "hash"



@dataclass
class Attribute:
    name: str
    attr_type: str          # raw string from JSON (e.g. "int", "varchar(50)")
    is_unique: bool
    n_distinct: int         # V(A, r) — number of distinct values

    def byte_size(self) -> int:
        t = self.attr_type.lower()
        if t == "int":
            return 4
        if t in ("float", "double"):
            return 8
        if t in ("date", "datetime"):
            return 8
        if t == "bool":
            return 1
        if t.startswith("varchar"):
            try:
                return int(t[t.index("(") + 1: t.index(")")])
            except (ValueError, IndexError):
                return 50
        return 8


@dataclass
class Index:
    attributes: list[str]          # one or more attribute names
    index_type: IndexType
    is_clustering: bool             # sortirajući / grupišući
    height: Optional[int] = None   # B+ tree height (None for hash)

    @property
    def first_attr(self) -> str:
        return self.attributes[0]


@dataclass
class Table:
    name: str
    attributes: list[Attribute]
    n_rows: int             # nr — broj redova
    n_blocks: int           # br — broj blokova
    rows_per_block: int     # fr — blocking factor
    indexes: list[Index] = field(default_factory=list)

    def get_attribute(self, attr_name: str) -> Optional[Attribute]:
        for a in self.attributes:
            if a.name.lower() == attr_name.lower():
                return a
        return None

    def get_indexes_for(self, attr_name: str) -> list[Index]:
        return [
            idx for idx in self.indexes
            if attr_name.lower() in [a.lower() for a in idx.attributes]
        ]

    def row_size(self) -> int:
        return sum(a.byte_size() for a in self.attributes)


@dataclass
class Schema:
    tables: list[Table]

    def get_table(self, name: str) -> Optional[Table]:
        for t in self.tables:
            if t.name.lower() == name.lower():
                return t
        return None


@dataclass
class IntermediateResult:
    """Statistics for a materialized intermediate result (after selection/join)."""
    label: str              # human-readable description, e.g. "Students ⋈ Enrollments"
    n_rows: int             # estimated row count
    n_blocks: int           # estimated block count
    attributes: list[str]   # available attribute names (qualified as table.attr)
    row_size: int           # estimated row size in bytes
    buffer_size: int        # B — inherited from query context

    @classmethod
    def from_table(cls, table: Table, buffer_size: int) -> "IntermediateResult":
        return cls(
            label=table.name,
            n_rows=table.n_rows,
            n_blocks=table.n_blocks,
            attributes=[f"{table.name}.{a.name}" for a in table.attributes],
            row_size=table.row_size(),
            buffer_size=buffer_size,
        )
