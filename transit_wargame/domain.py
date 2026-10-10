"""Explicit inputs, state, and outputs for the baseline model."""

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Callable

Edge = tuple[str, str]
DEFAULT_STARTING_BUDGET = 50.0  # m1.7: fixed cumulative capital, normalized units.


def edge_key(a: str, b: str) -> Edge:
    if a == b:
        raise ValueError("A corridor must connect two different nodes.")
    return tuple(sorted((a, b)))


class Mode(StrEnum):
    BUS = "bus"
    RAIL = "rail"


class TravelerModel(StrEnum):
    DISCRETE_TYPES = "discrete_types"
    LOGIT = "logit"

    @property
    def label(self) -> str:
        return "Discrete types" if self == self.DISCRETE_TYPES else "Logit"


@dataclass(frozen=True)
class Parameters:
    alpha_bus: float = 1.15
    alpha_rail: float = 0.75
    beta: float = 0.10
    bus_cost_per_km: float = 1.0
    rail_bus_cost_ratio: float = 5.0
    transfer_time: float = 0.0
    traveler_model: TravelerModel = TravelerModel.DISCRETE_TYPES
    pro_transit_share: float = 0.40
    pro_transit_tolerance: float = 1.50

    def __post_init__(self):
        object.__setattr__(self, "traveler_model", TravelerModel(self.traveler_model))
        for name in ("alpha_bus", "alpha_rail", "bus_cost_per_km", "rail_bus_cost_ratio"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and strictly positive.")
        if not isfinite(self.beta) or self.beta < 0:
            raise ValueError("beta must be finite and nonnegative.")
        if self.traveler_model == TravelerModel.LOGIT and self.beta == 0:
            raise ValueError("Logit beta must be strictly positive; logsum welfare is undefined at zero.")
        if not isfinite(self.pro_transit_share) or not 0 <= self.pro_transit_share <= 1:
            raise ValueError("pro_transit_share must be finite and in [0, 1].")
        if not isfinite(self.pro_transit_tolerance) or self.pro_transit_tolerance < 1:
            raise ValueError("pro_transit_tolerance must be finite and at least 1.")
        if self.transfer_time != 0:
            raise ValueError("Only zero transfer time is supported; transfer semantics are undefined.")


@dataclass(frozen=True)
class Node:
    name: str
    latitude: float
    longitude: float
    job_center: bool = False


@dataclass(frozen=True)
class Corridor:
    edge: Edge
    length_km: float
    buildable: bool = True
    bus_allowed: bool = True
    rail_allowed: bool = True
    initial_bus: bool = False
    initial_rail: bool = False

    def __post_init__(self):
        object.__setattr__(self, "edge", edge_key(*self.edge))
        if not isfinite(self.length_km) or self.length_km <= 0:
            raise ValueError("Corridor lengths must be finite and strictly positive.")


@dataclass(frozen=True)
class Demand:
    origin: str
    destination: str
    count: float

    def __post_init__(self):
        if not isfinite(self.count) or self.count < 0:
            raise ValueError("Commuting demand must be finite and nonnegative.")


@dataclass(frozen=True)
class Scenario:
    nodes: tuple[Node, ...]
    corridors: tuple[Corridor, ...]
    demands: tuple[Demand, ...]
    excluded: frozenset[Edge] = frozenset()

    def __post_init__(self):
        names = {node.name for node in self.nodes}
        if len(names) != len(self.nodes):
            raise ValueError("Duplicate node names.")
        for node in self.nodes:
            if not (isfinite(node.latitude) and -90 <= node.latitude <= 90
                    and isfinite(node.longitude) and -180 <= node.longitude <= 180):
                raise ValueError(f"Invalid coordinates for {node.name}.")
        edges = {c.edge for c in self.corridors}
        if len(edges) != len(self.corridors) or edges & self.excluded:
            raise ValueError("Duplicate or excluded corridor in scenario.")
        if any(set(c.edge) - names for c in self.corridors):
            raise ValueError("Corridor references an unknown node.")
        pairs = {(d.origin, d.destination) for d in self.demands}
        if len(pairs) != len(self.demands):
            raise ValueError("Duplicate OD pair; aggregate demand before loading.")
        if any({d.origin, d.destination} - names for d in self.demands):
            raise ValueError("Demand references an unknown node.")

    @property
    def node_lookup(self) -> dict[str, Node]:
        return {node.name: node for node in self.nodes}

    @property
    def corridor_lookup(self) -> dict[Edge, Corridor]:
        return {corridor.edge: corridor for corridor in self.corridors}


@dataclass(frozen=True)
class Action:
    edge: Edge
    mode: Mode

    def __post_init__(self):
        object.__setattr__(self, "edge", edge_key(*self.edge))
        object.__setattr__(self, "mode", Mode(self.mode))

    @property
    def key(self) -> tuple[str, str, str]:
        return (*self.edge, self.mode.value)

    @property
    def label(self) -> str:
        return f"{self.mode.value.title()} · {' ↔ '.join(self.edge)}"


@dataclass(frozen=True)
class Route:
    time: float
    nodes: tuple[str, ...] = ()
    modes: tuple[Mode, ...] = ()


@dataclass(frozen=True)
class TypeResponse:
    name: str
    population_share: float
    tolerance_ratio: float
    transit_cost: float
    driving_cost: float
    chooses_transit: bool
    utility: float


@dataclass(frozen=True)
class TravelerResponse:
    """Mode share and utility from one coherent traveler-choice policy."""

    transit_share: float
    welfare_per_commuter: float
    types: tuple[TypeResponse, ...] = ()

    def __post_init__(self):
        if not isfinite(self.transit_share) or not 0 <= self.transit_share <= 1:
            raise ValueError("Traveler-response policy must return a share in [0, 1].")
        if not isfinite(self.welfare_per_commuter):
            raise ValueError("Traveler-response welfare must be finite.")


@dataclass(frozen=True)
class TravelerOutcome:
    demand: Demand
    driving_time: float
    route: Route
    response: TravelerResponse

    @property
    def transit_share(self) -> float:
        return self.response.transit_share

    @property
    def welfare(self) -> float:
        return self.demand.count * self.response.welfare_per_commuter

    @property
    def transit_count(self) -> float:
        return self.demand.count * self.transit_share

    @property
    def driving_count(self) -> float:
        return self.demand.count * (1 - self.transit_share)


@dataclass(frozen=True)
class Evaluation:
    welfare: float
    actual_time: float
    average_time: float
    transit_share: float
    total_demand: float
    travelers: tuple[TravelerOutcome, ...]
    edge_flows: dict[Edge, float] = field(default_factory=dict)
    service_flows: dict[tuple[Edge, Mode], float] = field(default_factory=dict)
    type_transit_shares: dict[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class HistoryRecord:
    stage: int
    action: Action
    cost: float
    welfare_improvement: float
    score: float
    remaining_budget: float
    welfare: float
    actual_time: float
    actual_time_change: float
    transit_share: float


@dataclass(frozen=True)
class State:
    scenario: Scenario
    parameters: Parameters
    bus: frozenset[Edge]
    rail: frozenset[Edge]
    starting_budget: float
    spent: float = 0.0
    baseline_welfare: float = 0.0
    baseline_actual_time: float = 0.0
    history: tuple[HistoryRecord, ...] = ()

    @property
    def remaining_budget(self) -> float:
        return self.starting_budget - self.spent

    @property
    def stage(self) -> int:
        return len(self.history)


@dataclass(frozen=True)
class ActionEvaluation:
    action: Action
    cost: float
    welfare_improvement: float
    score: float
    affordable: bool
    remaining_after: float
    welfare_after: float
    actual_time_after: float
    actual_time_change: float


ResponsePolicy = Callable[[float, float, Parameters], TravelerResponse]
AgencyPolicy = Callable[[tuple[ActionEvaluation, ...]], ActionEvaluation | None]
