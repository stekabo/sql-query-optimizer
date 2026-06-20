from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Condition:
    """A single WHERE predicate: left OP right (e.g. Students.age > 18)."""
    left: str
    operator: str           # =, !=, <, <=, >, >=, LIKE
    right: str
    is_join_condition: bool = False

    def __str__(self):
        return f"{self.left} {self.operator} {self.right}"


@dataclass
class TableRef:
    """A table reference in the FROM clause, with optional alias."""
    name: str               # real table name
    alias: Optional[str]    # alias, or None

    @property
    def ref(self) -> str:
        """The name used to qualify attributes (alias if present, else name)."""
        return self.alias if self.alias else self.name


@dataclass
class ParsedQuery:
    select: list[str]
    from_tables: list[TableRef]
    where: list[Condition] = field(default_factory=list)
    order_by: Optional[str] = None

    def table_names(self) -> list[str]:
        return [t.name for t in self.from_tables]

    def alias_map(self) -> dict[str, str]:
        """Maps alias (or name) → real table name."""
        return {t.ref.lower(): t.name for t in self.from_tables}

    def join_conditions(self) -> list[Condition]:
        return [c for c in self.where if c.is_join_condition]

    def selection_conditions(self) -> list[Condition]:
        return [c for c in self.where if not c.is_join_condition]
