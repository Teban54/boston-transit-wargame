"""Geographic Plotly map overlays and tabular views; geometry stays model-independent."""

from math import atan, cos, degrees, exp, hypot, log, pi, radians, tan
from hashlib import sha256

import plotly.graph_objects as go

from .domain import Action, Evaluation, Mode, State, edge_key

COLORS = {Mode.RAIL: "#3569D4", Mode.BUS: "#C87C24"}
CANDIDATE_COLOR = "#8053A6"
LABEL_POSITIONS = {
    "Downtown": "top right", "Kendall": "bottom left", "Longwood": "bottom left",
    "Seaport": "bottom right", "Back Bay": "bottom center",
    "Cambridge/Harvard": "top left", "Somerville": "top left",
    "Everett/Chelsea": "top center", "Allston/Brookline": "bottom left",
    "Jamaica Plain": "top left", "Roslindale/West Roxbury": "bottom right",
    "Roxbury": "top right", "Dorchester/Mattapan": "bottom right",
    "East Boston/Airport": "top right",
}
MERCATOR_RADIUS_M = 6378137.0  # Rendering projection only; engine distances are unchanged.


def _project(coordinate):
    lon, lat = coordinate
    return (MERCATOR_RADIUS_M * radians(lon),
            MERCATOR_RADIUS_M * log(tan(pi / 4 + radians(lat) / 2)))


def _unproject(point):
    x, y = point
    return [degrees(x / MERCATOR_RADIUS_M), degrees(2 * atan(exp(y / MERCATOR_RADIUS_M)) - pi / 2)]


def _parallel_coordinates(coordinates, offset_m):
    """Offset a straight segment in map space without touching scenario inputs."""
    if not offset_m:
        return coordinates
    a, b = [_project(point) for point in coordinates]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = hypot(dx, dy)
    if not length:
        return coordinates
    latitude = sum(point[1] for point in coordinates) / 2
    offset = offset_m / cos(radians(latitude))
    ox, oy = -dy / length * offset, dx / length * offset
    return [_unproject((x + ox, y + oy)) for x, y in (a, b)]


def _line_layer(name, coordinates, color, width, dash=(), opacity=1.0):
    line = dict(width=width)
    if dash:
        line["dash"] = list(dash)
    return dict(name=name, visible=True, sourcetype="geojson", type="line", below="traces",
                source={"type": "Feature", "geometry": {"type": "LineString", "coordinates": coordinates}},
                color=color, line=line, opacity=opacity)


def _hover_trace(fig, name, coordinates, hover, uid, edge=None):
    a, b = [_project(point) for point in coordinates]
    targets = [_unproject((a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1])))
               for fraction in (0.25, 0.5, 0.75)]
    fig.add_trace(go.Scattermap(
        lon=[point[0] for point in targets], lat=[point[1] for point in targets],
        mode="markers", name=name, uid=uid, showlegend=False,
        marker=dict(size=18, color="rgba(0,0,0,0)"),
        text=[hover] * len(targets), hovertemplate="%{text}<extra></extra>",
        customdata=[{"kind": "corridor", "edge": list(edge)} if edge else {}] * len(targets),
    ))


def map_legend(show_candidates: bool = False) -> str:
    """Static swatches match native map layers, which are not Plotly legend traces."""
    items = []
    for mode in (Mode.RAIL, Mode.BUS):
        for initial in (True, False):
            label = f"{'Initial' if initial else 'New'} {mode.value}"
            border = "dashed" if mode == Mode.BUS else "solid"
            width = 3 if initial else 5
            items.append(f'<span><i style="width:28px;border-top:{width}px {border} {COLORS[mode]}"></i>{label}</span>')
    for label, fill in (("Job center", "#243855"), ("Residential origin", "white")):
        items.append(f'<span><i style="width:11px;height:11px;border:2px solid #243855;'
                     f'border-radius:50%;background:{fill}"></i>{label}</span>')
    items.append('<span><i style="width:28px;border-top:4px solid #159B86"></i>Preview / inspected route</span>')
    for label, color in (("Eligible connection", "#159B86"), ("Eligible, above budget", "#C88718")):
        items.append(f'<span><i style="width:14px;height:14px;border:3px solid {color};border-radius:50%"></i>{label}</span>')
    if show_candidates:
        items.append(f'<span><i style="width:28px;border-top:3px dotted {CANDIDATE_COLOR}"></i>Unserved candidate</span>')
    return ('<div class="network-legend" role="group" aria-label="Map legend">' + ''.join(items) + '</div>'
            '<style>.network-legend{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:12px;color:#243855;'
            'margin:4px 0 10px}.network-legend span{display:inline-flex;align-items:center;gap:7px}'
            '.network-legend i{display:inline-block;flex-shrink:0}</style>')


def map_figure(state: State, evaluation: Evaluation, show_candidates: bool = False,
               flow_widths: bool = False, selected: Action | None = None,
               route_nodes: tuple[str, ...] = (), view_revision: str = "boston-network",
               selection=None, eligible: tuple[dict, ...] = ()) -> go.Figure:
    nodes = state.scenario.nodes
    coordinates = {node.name: [node.longitude, node.latitude] for node in nodes}
    fig = go.Figure()
    layers = []
    max_flow = max(evaluation.edge_flows.values(), default=0) or 1
    for corridor in state.scenario.corridors:
        edge = corridor.edge
        endpoints = [coordinates[name] for name in edge]
        bus, rail = edge in state.bus, edge in state.rail
        if not bus and not rail:
            if show_candidates and corridor.buildable:
                category = "Unserved candidate"
                layers.append(_line_layer(f"{category}: {' ↔ '.join(edge)}", endpoints, CANDIDATE_COLOR, 3, (1, 2)))
                eligibility = " / ".join(mode.value for mode, allowed in
                                           ((Mode.BUS, corridor.bus_allowed), (Mode.RAIL, corridor.rail_allowed)) if allowed)
                _hover_trace(fig, category, endpoints,
                             f"{' ↔ '.join(edge)}<br>Unserved · eligible: {eligibility}", f"candidate:{edge}", edge)
            continue
        for mode, active, initial in ((Mode.RAIL, rail, corridor.initial_rail),
                                      (Mode.BUS, bus, corridor.initial_bus)):
            if not active:
                continue
            separation = 100 if flow_widths else 60
            offset = (separation if mode == Mode.RAIL else -separation) if bus and rail else 0
            service_coordinates = _parallel_coordinates(endpoints, offset)
            category = f"{'Initial' if initial else 'New'} {mode.value}"
            width = 2.6 if initial else 5.0
            if flow_widths:
                width += 3.0 * evaluation.edge_flows[edge] / max_flow
            service_flow = evaluation.service_flows.get((edge, mode), 0.0)
            hover = (f"<b>{' ↔ '.join(edge)}</b><br>{category}"
                     f"<br>Length proxy: {corridor.length_km:.3f} km"
                     f"<br>Corridor transit flow: {evaluation.edge_flows[edge]:,.1f}"
                     f"<br>{mode.value.title()} flow: {service_flow:,.1f}"
                     f"<br>Active service: {'bus + rail' if bus and rail else mode.value}")
            layers.append(_line_layer(f"{category}: {' ↔ '.join(edge)}", service_coordinates,
                                      COLORS[mode], width, (3, 2) if mode == Mode.BUS else ()))
            _hover_trace(fig, category, service_coordinates, hover, f"service:{edge}:{mode.value}", edge)
    eligible_nodes = {}
    for option in eligible:
        edge = option["edge"]
        endpoints = [coordinates[name] for name in edge]
        other = next(name for name in edge if name != selection.first)
        eligible_nodes[other] = option
        color = "#159B86" if option["affordable"] else "#C88718"
        fig.add_trace(go.Scattermap(lon=[p[0] for p in endpoints], lat=[p[1] for p in endpoints],
                                   mode="lines", line=dict(color=color, width=2), opacity=.75,
                                   name="Eligible connection", uid=f"eligible:{edge}", hoverinfo="skip", showlegend=False))
        _hover_trace(fig, "Eligible connection", endpoints,
                     f"{' ↔ '.join(edge)}<br>Eligible: {' / '.join(option['modes'])}"
                     f"<br>{'Within budget' if option['affordable'] else 'Above budget'} · click to select",
                     f"eligible-target:{edge}", edge)
    highlights = []
    if selected:
        highlights.append((selected.edge, "Project preview"))
    highlights.extend((edge_key(a, b), "Inspected transit route")
                      for a, b in zip(route_nodes, route_nodes[1:]))
    for edge, category in highlights:
        endpoints = [coordinates[name] for name in edge]
        # A geographic trace keeps highlights above all native corridor layers
        # while the following hover targets and vertices stay above the stroke.
        fig.add_trace(go.Scattermap(
            lon=[p[0] for p in endpoints], lat=[p[1] for p in endpoints],
            mode="lines", line=dict(color="#159B86", width=5), opacity=1,
            name=category, uid=f"highlight-stroke:{category}:{edge}",
            hoverinfo="skip", showlegend=False,
        ))
        _hover_trace(fig, category, endpoints, f"{category}: {' ↔ '.join(edge)}", f"highlight:{category}:{edge}", edge)
    if eligible_nodes:
        group = [node for node in nodes if node.name in eligible_nodes]
        fig.add_trace(go.Scattermap(lon=[n.longitude for n in group], lat=[n.latitude for n in group],
                                   mode="markers", marker=dict(size=25, color=["#159B86" if eligible_nodes[n.name]["affordable"]
                                                                  else "#C88718" for n in group], allowoverlap=True),
                                   name="Eligible locations", uid="eligible-nodes", hoverinfo="skip", showlegend=False))
    selected_nodes = {n for n in (selection.first, selection.second) if n} if selection else set()
    if selected_nodes:
        group = [n for n in nodes if n.name in selected_nodes]
        fig.add_trace(go.Scattermap(lon=[n.longitude for n in group], lat=[n.latitude for n in group],
                                   mode="markers", marker=dict(size=31, color="#159B86", allowoverlap=True),
                                   name="Selected locations", uid="selected-nodes", hoverinfo="skip", showlegend=False))
    def node_color(node):
        return "#B8C2CD" if selection and selection.first and not selection.second and (
            node.name not in eligible_nodes and node.name != selection.first) else "#243855"
    # A second circle makes residential markers hollow without relocating their centers.
    residents = [node for node in nodes if not node.job_center]
    if residents:
        fig.add_trace(go.Scattermap(lon=[node.longitude for node in residents], lat=[node.latitude for node in residents],
                                    mode="markers", marker=dict(size=15, color=[node_color(n) for n in residents]),
                                    name="Residential marker outlines", uid="resident-outlines", hoverinfo="skip", showlegend=False))
    for jobs in (False, True):
        group = [n for n in nodes if n.job_center == jobs]
        if not group:
            continue
        fig.add_trace(go.Scattermap(
            lon=[n.longitude for n in group], lat=[n.latitude for n in group],
            mode="markers", name="Job center" if jobs else "Residential origin", uid=f"nodes:{jobs}",
            showlegend=False, ids=[n.name for n in group],
            marker=dict(size=13 if jobs else 11, color=[node_color(n) for n in group] if jobs else "#FFFFFF",
                        allowoverlap=True),
            text=[f"<b>{n.name}</b><br>{'Job center' if jobs else 'Residential origin'}"
                  f"<br>{n.latitude:.7f}, {n.longitude:.7f}" + (
                      f"<br>Eligible: {' / '.join(eligible_nodes[n.name]['modes'])}"
                      f" · {'within budget' if eligible_nodes[n.name]['affordable'] else 'above budget'}"
                      if n.name in eligible_nodes else "") for n in group],
            customdata=[{"kind": "node", "node": n.name} for n in group],
            hovertemplate="%{text}<extra></extra>",
        ))
    for node in nodes:
        # Plotly's map text offsets require a marker size even for invisible anchors.
        fig.add_trace(go.Scattermap(lon=[node.longitude], lat=[node.latitude], mode="markers+text",
                                    marker=dict(size=18, color="rgba(0,0,0,0)"),
                                    text=[node.name.replace("/", "/<br>")],
                                    textposition=LABEL_POSITIONS.get(node.name, "top center"),
                                    textfont=dict(size=12, color="#243855", weight=600),
                                    name=f"Label: {node.name}", uid=f"label:{node.name}",
                                    customdata=[{"kind": "node", "node": node.name}],
                                    hoverinfo="skip", showlegend=False))
    # Plotly uses uid in CSS selectors when removing traces during react().
    # Logical corridor/node names remain deterministic but must not contain
    # selector punctuation, spaces, or quotes in the serialized identifier.
    for trace in fig.data:
        trace.uid = "t" + sha256(trace.uid.encode("utf-8")).hexdigest()[:24]
        # Set every trace's ordering explicitly. Plotly's map lookup otherwise
        # carries a previous trace's below value into subsequent marker traces.
        trace.below = ""
    vertex_anchor = next(t for t in fig.data if t.name in (
        "Eligible locations", "Selected locations", "Residential marker outlines", "Residential origin", "Job center"))
    strokes = [t for t in fig.data if t.mode == "lines"]
    for trace in fig.data:
        if trace.mode == "lines":
            trace.below = f"plotly-trace-layer-{vertex_anchor.uid}-circle"
    # Explicit anchors avoid Plotly's generic "traces" lookup changing meaning
    # when trace objects are retained/reordered by react(). Corridors stay below
    # the highlight stroke, and the stroke stays below all vertex markers.
    line_anchor = (f"plotly-trace-layer-{strokes[0].uid}-line" if strokes else
                   f"plotly-trace-layer-{vertex_anchor.uid}-circle")
    for layer in layers:
        layer["below"] = line_anchor
    center = dict(lat=(min(n.latitude for n in nodes) + max(n.latitude for n in nodes)) / 2,
                  lon=(min(n.longitude for n in nodes) + max(n.longitude for n in nodes)) / 2)
    fig.update_layout(
        height=600, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="#F7F8FB",
        map=dict(style="carto-positron", center=center, zoom=11, pitch=0, bearing=0,
                 layers=layers, uirevision=view_revision),
        font=dict(family="Arial, sans-serif", color="#243855"),
        showlegend=False, dragmode="pan",
        hoverlabel=dict(bgcolor="white", font_size=12), uirevision=view_revision,
    )
    return fig


def corridor_rows(state: State, evaluation: Evaluation) -> list[dict]:
    rows = []
    for corridor in state.scenario.corridors:
        services = []
        for mode, active, initial in ((Mode.BUS, corridor.edge in state.bus, corridor.initial_bus),
                                      (Mode.RAIL, corridor.edge in state.rail, corridor.initial_rail)):
            if active:
                services.append(f"{'Initial' if initial else 'New'} {mode.value}")
        rows.append({"Corridor": " ↔ ".join(corridor.edge), "Service": " + ".join(services) or "Unserved",
                     "Length proxy (km)": corridor.length_km,
                     "Transit flow": evaluation.edge_flows[corridor.edge],
                     "Bus flow": evaluation.service_flows.get((corridor.edge, Mode.BUS), 0.0),
                     "Rail flow": evaluation.service_flows.get((corridor.edge, Mode.RAIL), 0.0)})
    return rows
