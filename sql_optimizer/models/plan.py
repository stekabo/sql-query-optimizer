from dataclasses import dataclass, field
from typing import Optional

from sql_optimizer.models.schema import IntermediateResult


@dataclass
class OperationStep:
    description: str
    algorithm: str
    cost: int
    details: str = ""


@dataclass
class EvaluationPlan:
    query: str
    steps: list[OperationStep] = field(default_factory=list)
    final_result: Optional[IntermediateResult] = None

    @property
    def total_cost(self) -> int:
        return sum(s.cost for s in self.steps)
