"""Manual project selection; presentation state never changes the simulation."""

from dataclasses import dataclass

from .domain import Action, Edge, Mode, State, edge_key
from .engine import enumerate_actions


@dataclass(frozen=True)
class ManualSelection:
    first: str | None = None
    second: str | None = None
    mode: Mode = Mode.BUS
    notice: str = ""

    @property
    def edge(self) -> Edge | None:
        return edge_key(self.first, self.second) if self.first and self.second else None

    @property
    def action(self) -> Action | None:
        return Action(self.edge, self.mode) if self.edge else None


def select_edge(state: State, edge: Edge, preferred: Mode = Mode.BUS) -> ManualSelection:
    legal = [a for a in enumerate_actions(state) if a.edge == edge]
    if not legal:
        return ManualSelection(notice="This corridor has no legal construction project.")
    mode = preferred if any(a.mode == preferred for a in legal) else legal[0].mode
    return ManualSelection(*edge, mode)


def select_event(state: State, current: ManualSelection, event: dict) -> ManualSelection:
    """Validate browser inputs against the current network, independent of budget."""
    if not isinstance(event, dict):
        return current
    if event.get("kind") == "clear":
        return ManualSelection()
    if event.get("kind") == "corridor":
        pair = event.get("edge")
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or not all(isinstance(n, str) for n in pair):
            return current
        if pair[0] == pair[1]:
            return current
        edge = edge_key(*pair)
        if edge not in state.scenario.corridor_lookup:
            return ManualSelection(current.first, current.second, current.mode,
                                   "This connection is outside the buildable network.")
        chosen = select_edge(state, edge, current.mode)
        return chosen if chosen.edge else ManualSelection(current.first, current.second, current.mode, chosen.notice)
    node = event.get("node")
    if event.get("kind") != "node" or not isinstance(node, str) or node not in state.scenario.node_lookup:
        return current
    if node == current.first and not current.second:
        return ManualSelection()
    if not current.first or current.second:
        legal = any(node in a.edge for a in enumerate_actions(state))
        return ManualSelection(node, notice="" if legal else "This location has no legal connections left.")
    edge = edge_key(current.first, node)
    if not any(a.edge == edge for a in enumerate_actions(state)):
        return ManualSelection(current.first, mode=current.mode,
                               notice="Choose a highlighted location; this connection has no legal project.")
    chosen = select_edge(state, edge, current.mode)
    # Retain the user's first endpoint even though model edges are undirected/sorted.
    return ManualSelection(current.first, node, chosen.mode)


def mode_unavailable_reason(state: State, edge: Edge, mode: Mode) -> str:
    if Action(edge, mode) in enumerate_actions(state):
        return ""
    corridor = state.scenario.corridor_lookup.get(edge)
    if corridor is None or edge in state.scenario.excluded:
        return "Corridor is excluded or unavailable."
    if edge in state.rail:
        return "Rail already exists." if mode == Mode.RAIL else "Bus cannot be added alongside existing rail."
    if mode == Mode.BUS and edge in state.bus:
        return "Bus already exists. Rail can be added while retaining bus."
    if not corridor.buildable:
        return "Corridor is not buildable."
    return f"{mode.value.title()} is not eligible on this corridor."


def selection_overlay(state: State, current: ManualSelection, projects) -> tuple[dict, ...]:
    """Eligible edges adjacent to the first endpoint, including over-budget edges."""
    if not current.first or current.second:
        return ()
    grouped = {}
    for project in projects:
        if current.first in project.action.edge:
            grouped.setdefault(project.action.edge, []).append(project)
    return tuple({"edge": edge, "affordable": any(p.affordable for p in values),
                  "modes": tuple(p.action.mode.value for p in values)}
                 for edge, values in sorted(grouped.items()))


def interaction_payload(state: State, current: ManualSelection, mode: str, context: str) -> dict:
    return {"enabled": mode == "Manual", "context": context,
            "first": current.first, "second": current.second,
            "status": (current.notice or (f"Selected: {current.first} ↔ {current.second}. Choose Bus or Rail in Agency."
                       if current.second else f"From {current.first}: choose a highlighted location."
                       if current.first else "Manual: click a location, then a highlighted second location."))
                       if mode == "Manual" else "Automatic: use Step or Play in Agency."}
