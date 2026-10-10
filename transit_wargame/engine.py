"""Pure routing, traveler response, action scoring, and capital transitions."""

from dataclasses import replace
from heapq import heappop, heappush
from math import exp, fsum, inf, isfinite, log1p

from .data import distance_km
from .domain import (
    DEFAULT_STARTING_BUDGET, Action, ActionEvaluation, Evaluation, HistoryRecord, Mode, Parameters,
    ResponsePolicy, Route, Scenario, State, TravelerModel, TravelerOutcome,
    TravelerResponse, TypeResponse, edge_key,
)


def logit_share(transit_time: float, driving_time: float, beta: float) -> float:
    if not isfinite(transit_time):
        return 0.0
    # The two branches avoid exponent overflow and exp(-large)/exp(-large).
    difference = 0.0 if beta == 0 else beta * (transit_time - driving_time)
    if difference >= 0:
        term = exp(-difference)
        return term / (1 + term)
    return 1 / (1 + exp(difference))


def logit_welfare(transit_time: float, driving_time: float, beta: float) -> float:
    """Logsum utility per commuter, including only available alternatives."""
    if not isfinite(beta) or beta <= 0:
        raise ValueError("Logit beta must be finite and strictly positive.")
    if not isfinite(transit_time):
        return -driving_time
    return -min(driving_time, transit_time) + log1p(exp(-beta * abs(transit_time - driving_time))) / beta


def discrete_response(transit_time: float, driving_time: float, parameters: Parameters) -> TravelerResponse:
    types = []
    for name, share, ratio in (("proT", parameters.pro_transit_share, parameters.pro_transit_tolerance),
                               ("proD", 1 - parameters.pro_transit_share, 1.0)):
        cost = transit_time / ratio
        # Exact utility ties favor transit; unavailable transit always loses.
        chooses_transit = isfinite(transit_time) and cost <= driving_time
        types.append(TypeResponse(name, share, ratio, cost, driving_time, chooses_transit,
                                  -cost if chooses_transit else -driving_time))
    return TravelerResponse(fsum(t.population_share for t in types if t.chooses_transit),
                            fsum(t.population_share * t.utility for t in types), tuple(types))


def traveler_response(transit_time: float, driving_time: float, parameters: Parameters) -> TravelerResponse:
    if parameters.traveler_model == TravelerModel.DISCRETE_TYPES:
        return discrete_response(transit_time, driving_time, parameters)
    return TravelerResponse(logit_share(transit_time, driving_time, parameters.beta),
                            logit_welfare(transit_time, driving_time, parameters.beta))


def active_graph(state: State) -> dict:
    graph = {node.name: [] for node in state.scenario.nodes}
    for corridor in state.scenario.corridors:
        options = []
        # Rail first resolves equal bus/rail times without changing stored service.
        if corridor.edge in state.rail:
            options.append((state.parameters.alpha_rail * corridor.length_km, Mode.RAIL))
        if corridor.edge in state.bus:
            options.append((state.parameters.alpha_bus * corridor.length_km, Mode.BUS))
        if options:
            time, mode = min(options, key=lambda option: option[0])
            a, b = corridor.edge
            graph[a].append((b, time, mode))
            graph[b].append((a, time, mode))
    return {node: sorted(neighbors) for node, neighbors in graph.items()}


def shortest_paths(graph: dict, origin: str) -> dict[str, Route]:
    """Dijkstra with lexicographic complete-node-path tie breaking."""
    if origin not in graph:
        raise ValueError(f"Unknown route origin: {origin}")
    best = {origin: (0.0, (origin,))}
    queue = [(0.0, (origin,), ())]
    routes = {}
    while queue:
        time, path, modes = heappop(queue)
        node = path[-1]
        if best[node] != (time, path):
            continue
        routes[node] = Route(time, path, modes)
        for neighbor, edge_time, mode in graph[node]:
            candidate = (time + edge_time, path + (neighbor,))
            if neighbor not in best or candidate < best[neighbor]:
                best[neighbor] = candidate
                heappush(queue, (*candidate, modes + (mode,)))
    return routes


def evaluate_network(state: State, response_policy: ResponsePolicy | None = None) -> Evaluation:
    graph = active_graph(state)
    lookup = state.scenario.node_lookup
    paths = {origin: shortest_paths(graph, origin)
             for origin in sorted({d.origin for d in state.scenario.demands})}
    travelers = []
    actual_times = []
    edge_flows = {c.edge: 0.0 for c in state.scenario.corridors}
    service_flows = {}
    for demand in state.scenario.demands:
        driving = distance_km(lookup[demand.origin], lookup[demand.destination])
        route = paths[demand.origin].get(demand.destination, Route(inf))
        policy = response_policy or traveler_response
        # A custom policy cannot assign riders to an unavailable transit path.
        response = policy(route.time, driving, state.parameters) if isfinite(route.time) else traveler_response(
            route.time, driving, state.parameters)
        outcome = TravelerOutcome(demand, driving, route, response)
        travelers.append(outcome)
        # Disconnected OD demand contributes only driving time, never 0 * infinity.
        average = (response.transit_share * route.time + (1 - response.transit_share) * driving
                   if isfinite(route.time) else driving)
        actual_times.append(demand.count * average)
        for a, b, mode in zip(route.nodes, route.nodes[1:], route.modes):
            edge = edge_key(a, b)
            edge_flows[edge] += outcome.transit_count
            service_flows[(edge, mode)] = service_flows.get((edge, mode), 0.0) + outcome.transit_count
    total = fsum(d.count for d in state.scenario.demands)
    actual_time = fsum(actual_times)
    transit = fsum(outcome.transit_count for outcome in travelers)
    type_names = {t.name for out in travelers for t in out.response.types}
    if not travelers and response_policy is None and state.parameters.traveler_model == TravelerModel.DISCRETE_TYPES:
        type_names = {"proT", "proD"}
    type_shares = {}
    for name in sorted(type_names):
        masses = [(out.demand.count * t.population_share, t.chooses_transit)
                  for out in travelers for t in out.response.types if t.name == name]
        population = fsum(mass for mass, _ in masses)
        type_shares[name] = fsum(mass for mass, chosen in masses if chosen) / population if population else None
    return Evaluation(welfare=fsum(out.welfare for out in travelers), actual_time=actual_time,
                      average_time=actual_time / total if total else 0.0,
                      transit_share=transit / total if total else 0.0, total_demand=total,
                      travelers=tuple(travelers), edge_flows=edge_flows, service_flows=service_flows,
                      type_transit_shares=type_shares)


def enumerate_actions(state: State) -> tuple[Action, ...]:
    """Legal projects regardless of remaining budget."""
    actions = []
    for corridor in state.scenario.corridors:
        if not corridor.buildable or corridor.edge in state.scenario.excluded:
            continue
        bus, rail = corridor.edge in state.bus, corridor.edge in state.rail
        if corridor.bus_allowed and not bus and not rail:
            actions.append(Action(corridor.edge, Mode.BUS))
        if corridor.rail_allowed and not rail:
            actions.append(Action(corridor.edge, Mode.RAIL))
    return tuple(sorted(actions, key=lambda action: action.key))


def construction_cost(state: State, action: Action) -> float:
    corridor = state.scenario.corridor_lookup.get(action.edge)
    if corridor is None:
        raise ValueError("Unknown construction corridor.")
    multiplier = state.parameters.rail_bus_cost_ratio if action.mode == Mode.RAIL else 1.0
    cost = corridor.length_km * state.parameters.bus_cost_per_km * multiplier
    if not isfinite(cost) or cost <= 0:
        raise ValueError("Construction cost must be finite and strictly positive.")
    return cost


def affordable(state: State, action: Action) -> bool:
    return construction_cost(state, action) <= state.remaining_budget


def percentile(values: list[float], fraction: float) -> float:
    """Linear interpolation between adjacent sorted order statistics."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (index - lower) * (ordered[upper] - ordered[lower])


def derive_budget(state: State) -> float:
    """Optional legacy comparison: canonical initial feasible actions (MVP §12)."""
    initial = replace(state, bus=frozenset(c.edge for c in state.scenario.corridors if c.initial_bus),
                      rail=frozenset(c.edge for c in state.scenario.corridors if c.initial_rail))
    actions = enumerate_actions(initial)
    rail_costs = [construction_cost(initial, a) for a in actions if a.mode == Mode.RAIL]
    bus_costs = sorted(construction_cost(initial, a) for a in actions if a.mode == Mode.BUS)
    return max(percentile(rail_costs, 0.75), fsum(bus_costs[:3]))


def initialize_scenario(scenario: Scenario, parameters: Parameters = Parameters(),
                        starting_budget: float | None = DEFAULT_STARTING_BUDGET,
                        response_policy: ResponsePolicy | None = None) -> State:
    """Start with fixed B=50 by default; explicit None selects derived capital."""
    state = State(scenario, parameters,
                  frozenset(c.edge for c in scenario.corridors if c.initial_bus),
                  frozenset(c.edge for c in scenario.corridors if c.initial_rail), 0.0)
    budget = derive_budget(state) if starting_budget is None else starting_budget
    if not isfinite(budget) or budget < 0:
        raise ValueError("Starting budget must be finite and nonnegative.")
    state = replace(state, starting_budget=budget)
    evaluation = evaluate_network(state, response_policy)
    return replace(state, baseline_welfare=evaluation.welfare, baseline_actual_time=evaluation.actual_time)


def _add_service(state: State, action: Action) -> State:
    if action.mode == Mode.BUS:
        return replace(state, bus=state.bus | {action.edge})
    return replace(state, rail=state.rail | {action.edge})


def score_actions(state: State, response_policy: ResponsePolicy | None = None) -> tuple[ActionEvaluation, ...]:
    before = evaluate_network(state, response_policy)
    results = []
    for action in enumerate_actions(state):
        cost = construction_cost(state, action)
        after = evaluate_network(_add_service(state, action), response_policy)
        improvement = after.welfare - before.welfare
        results.append(ActionEvaluation(action, cost, improvement, improvement / cost,
                                        cost <= state.remaining_budget, state.remaining_budget - cost,
                                        after.welfare, after.actual_time, after.actual_time - before.actual_time))
    return tuple(results)


def choose_welfare_per_cost_action(evaluations: tuple[ActionEvaluation, ...]) -> ActionEvaluation | None:
    candidates = [e for e in evaluations if e.affordable and e.welfare_improvement > 0]
    return min(candidates, key=lambda e: (-e.score, e.action.key)) if candidates else None


def choose_max_immediate_welfare_action(evaluations: tuple[ActionEvaluation, ...]) -> ActionEvaluation | None:
    candidates = [e for e in evaluations if e.affordable and e.welfare_improvement > 0]
    return min(candidates, key=lambda e: (-e.welfare_improvement, e.action.key)) if candidates else None


def apply_action(state: State, action: Action, response_policy: ResponsePolicy | None = None) -> State:
    if action not in enumerate_actions(state):
        raise ValueError(f"Illegal action: {action.label}")
    cost = construction_cost(state, action)
    if cost > state.remaining_budget:
        raise ValueError(f"Unaffordable action: costs {cost:.6f}, remaining {state.remaining_budget:.6f}.")
    before = evaluate_network(state, response_policy)
    after_state = replace(_add_service(state, action), spent=fsum((state.spent, cost)))
    after = evaluate_network(after_state, response_policy)
    improvement = after.welfare - before.welfare
    record = HistoryRecord(state.stage + 1, action, cost, improvement, improvement / cost,
                           after_state.remaining_budget, after.welfare, after.actual_time,
                           after.actual_time - before.actual_time, after.transit_share)
    return replace(after_state, history=state.history + (record,))
