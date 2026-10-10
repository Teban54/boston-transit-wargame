"""Launch from the workspace: python -m streamlit run app.py"""

from dataclasses import replace
from math import isfinite
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from transit_wargame import controller
from transit_wargame.data import load_boston_scenario
from transit_wargame.domain import DEFAULT_STARTING_BUDGET, Action, Mode, TravelerModel
from transit_wargame.engine import (
    choose_max_immediate_welfare_action, choose_welfare_per_cost_action, derive_budget,
    evaluate_network, initialize_scenario, score_actions,
)
from transit_wargame.presentation import corridor_rows, map_figure, map_legend
from transit_wargame.map_view import render_map
from transit_wargame.selection import (
    ManualSelection, interaction_payload, mode_unavailable_reason, select_edge,
    select_event, selection_overlay,
)

st.set_page_config(page_title="Boston transit wargame", page_icon="🚆", layout="wide",
                   initial_sidebar_state="collapsed")

FIXED_BUDGET_MODE = "Fixed starting budget"
DERIVED_BUDGET_MODE = "Derived from project costs"
AGENCY_POLICIES = {
    "Welfare improvement per cost": choose_welfare_per_cost_action,
    "Maximum immediate welfare": choose_max_immediate_welfare_action,
}


def active_agency_policy():
    return AGENCY_POLICIES[st.session_state.agency_policy]


def change_agency_policy():
    st.session_state.demo = controller.pause(st.session_state.demo)


def automatic_ranking_key(project):
    metric = (project.welfare_improvement
              if active_agency_policy() is choose_max_immediate_welfare_action else project.score)
    return (-metric, project.action.key)


def reset_run():
    st.session_state.demo = controller.restart(st.session_state.demo)
    st.session_state.scenario_revision += 1
    clear_selection()
    sync_settings()


def sync_settings():
    """Restore applied values before creating widgets, including formerly hidden inputs."""
    state = st.session_state.demo.state
    params = state.parameters
    for key, value in {"traveler_model": params.traveler_model, "alpha_bus": params.alpha_bus,
                       "alpha_rail": params.alpha_rail, "beta": params.beta,
                       "pro_transit_share": params.pro_transit_share,
                       "pro_transit_tolerance": params.pro_transit_tolerance,
                       "cost_ratio": params.rail_bus_cost_ratio,
                       "budget_override": st.session_state.fixed_starting_budget,
                       "budget_mode": st.session_state.active_budget_mode}.items():
        st.session_state[key] = value


def change_traveler_model():
    st.session_state.demo = controller.switch_traveler_model(
        st.session_state.demo, st.session_state.traveler_model)
    st.session_state.scenario_revision += 1
    clear_selection()
    sync_settings()


def change_mode():
    st.session_state.demo = controller.switch_mode(st.session_state.demo, st.session_state.control_mode)
    clear_selection()


def project_widget_key():
    return f"project_choice_{st.session_state.scenario_revision}_{st.session_state.demo.state.stage}"


def selection_context():
    return f"{st.session_state.scenario_revision}:{st.session_state.demo.state.stage}:{st.session_state.selection_epoch}"


def clear_selection():
    st.session_state.manual_selection = ManualSelection()
    st.session_state.selection_epoch += 1
    st.session_state[project_widget_key()] = None


def commit_map_selection():
    event = st.session_state.network_map.get("selection")
    if (not isinstance(event, dict) or event.get("context") != selection_context()
            or st.session_state.demo.mode != "Manual"):
        return
    current = st.session_state.manual_selection
    chosen = select_event(st.session_state.demo.state, current, event)
    st.session_state.manual_selection = chosen
    st.session_state[project_widget_key()] = chosen.action.label if chosen.action else None


def choose_from_list():
    label = st.session_state[project_widget_key()]
    projects = score_actions(st.session_state.demo.state)
    project = next((p for p in projects if p.action.label == label), None)
    st.session_state.manual_selection = (select_edge(st.session_state.demo.state, project.action.edge, project.action.mode)
                                        if project else ManualSelection())


def choose_construction_mode(mode):
    current = st.session_state.manual_selection
    if current.edge and not mode_unavailable_reason(st.session_state.demo.state, current.edge, mode):
        st.session_state.manual_selection = ManualSelection(current.first, current.second, mode)
        st.session_state[project_widget_key()] = st.session_state.manual_selection.action.label


def recenter_map():
    st.session_state.map_view_revision += 1


def build_project(action):
    if (not isinstance(action, Action) or st.session_state.demo.mode != "Manual"
            or action != st.session_state.manual_selection.action):
        return
    try:
        st.session_state.demo = controller.manual_build(st.session_state.demo, action)
        clear_selection()
    except ValueError as exc:
        current = st.session_state.manual_selection
        st.session_state.manual_selection = ManualSelection(current.first, current.second, current.mode, str(exc))


def advance_once():
    st.session_state.demo = controller.step(controller.pause(st.session_state.demo), active_agency_policy())


def toggle_playback():
    session = st.session_state.demo
    st.session_state.demo = (controller.pause(session) if session.playing
                            else controller.play(session, time.monotonic(), active_agency_policy()))


def playback_tick():
    current = st.session_state.demo
    updated = controller.tick(current, time.monotonic(), active_agency_policy())
    if updated is not current:
        st.session_state.demo = updated
        st.rerun()


def project_preview(project):
    st.markdown(f"**{project.action.mode.value.title()} construction**")
    st.write(" ↔ ".join(project.action.edge))
    values = st.columns(2)
    values[0].metric("Project cost", f"{project.cost:.3f}")
    values[1].metric("Project welfare ΔW", f"{project.welfare_improvement:+,.1f}")
    st.caption(f"Capital after build: {project.remaining_after:.3f} normalized units")
    with st.expander("Project details"):
        st.table(pd.DataFrame({
            "Value": [f"{project.cost:.3f}", f"{project.remaining_after:.3f}",
                      f"{project.welfare_improvement:+,.2f}", f"{project.score:+,.2f}",
                      f"{project.actual_time_change:+,.2f}"],
        }, index=["Construction cost", "Capital after build", "Welfare improvement ΔW", "Welfare / cost",
                  "Actual-time change ΔH"]))
        st.caption("Positive ΔW improves welfare. Positive ΔH increases actual travel-time proxy. "
                   "Cost uses normalized units. Rail added to bus retains both services.")
    if not project.affordable:
        st.warning("This project exceeds the remaining capital.")
    elif project.welfare_improvement <= 0:
        st.info("This project has no immediate welfare improvement. Manual mode can still build it.")



def open_explore():
    st.session_state.explore_open = True


def close_explore():
    st.session_state.explore_open = False


def commit_inspector():
    st.session_state.od_index = st.session_state._od_index
    st.session_state.highlight_route = st.session_state._highlight_route
    st.session_state.inspector_changed = True


def statistics(state, evaluation):
    top = st.columns(2)
    top[0].metric("Stage / actions built", str(state.stage))
    top[1].metric("Remaining capital", f"{state.remaining_budget:.3f}")
    lower = st.columns(2)
    lower[0].metric("Welfare improvement from start", f"{evaluation.welfare - state.baseline_welfare:+,.1f}")
    lower[1].metric("Transit mode share", f"{evaluation.transit_share:.1%}")
    with st.expander("Details"):
        st.metric("Aggregate welfare W", f"{evaluation.welfare:,.1f}")
        st.metric("Spent capital", f"{state.spent:.3f}")
        st.metric("Aggregate actual travel-time proxy H", f"{evaluation.actual_time:,.1f}")
        st.metric("Average actual travel-time proxy", f"{evaluation.average_time:.3f}")
        st.metric("Actual-time change from start", f"{evaluation.actual_time - state.baseline_actual_time:+,.1f}")
        if state.parameters.traveler_model == TravelerModel.DISCRETE_TYPES:
            for name in ("proT", "proD"):
                share = evaluation.type_transit_shares[name]
                st.metric(f"Transit share within {name}", "N/A" if share is None else f"{share:.1%}")
        st.markdown("**Most recent action**")
        st.write(state.history[-1].action.label if state.history else "Canonical starting network")
        st.caption(f"{evaluation.total_demand:,.0f} modeled commuters · 45 OD pairs · 14 locations")
        st.caption("Welfare uses driving-time-equivalent utility units; higher is better. "
                   "Actual-time change is H − H at start; positive means more travel time. "
                   "Travel times use geographic proxies; capital uses normalized cost units.")


def scenario_settings():
    st.caption("Temporary parameters for sensitivity exploration.")
    st.button("Reset scenario", on_click=reset_run, width="stretch")
    st.selectbox("Traveler model", list(TravelerModel), format_func=lambda model: model.label,
                 key="traveler_model", on_change=change_traveler_model)
    st.caption("Switching models starts a new run. Reset returns to Discrete types with applied numeric settings.")
    params = st.session_state.demo.state.parameters
    with st.form("parameters"):
        bus = st.number_input("Bus travel-time multiplier", min_value=0.01,
                              step=0.05, key="alpha_bus")
        rail = st.number_input("Rail travel-time multiplier", min_value=0.01,
                               step=0.05, key="alpha_rail")
        beta, share, tolerance = params.beta, params.pro_transit_share, params.pro_transit_tolerance
        if params.traveler_model == TravelerModel.DISCRETE_TYPES:
            share = st.number_input("proT population share s", min_value=0.0, max_value=1.0,
                                    step=0.05, key="pro_transit_share")
            tolerance = st.number_input("proT tolerance ratio r", min_value=1.0,
                                        step=0.05, key="pro_transit_tolerance")
            st.caption("proD tolerance r = 1.00 (fixed). proT's share is a known population share.")
        else:
            beta = st.number_input("Logit sensitivity β", min_value=0.0, step=0.01, key="beta",
                                   help="Must be strictly positive for logsum welfare. Zero is rejected on application.")
        ratio = st.number_input("Rail / bus construction-cost ratio", min_value=0.01,
                                step=0.5, key="cost_ratio")
        budget_mode = st.radio("Starting capital", [FIXED_BUDGET_MODE, DERIVED_BUDGET_MODE],
                               key="budget_mode")
        override = st.number_input("Fixed starting capital B", min_value=0.0,
                                   step=1.0, format="%.6f", key="budget_override",
                                   help="Canonical default: B=50 normalized units. Used only in Fixed starting budget mode.")
        st.caption("Fixed B is one cumulative stock of capital. Derived capital is an optional comparison. "
                   "Budget changes take effect when you apply settings and restart.")
        submitted = st.form_submit_button("Apply parameters and restart", width="stretch")
    if submitted:
        try:
            parameters = replace(params, alpha_bus=bus, alpha_rail=rail, beta=beta,
                                 pro_transit_share=share, pro_transit_tolerance=tolerance,
                                 rail_bus_cost_ratio=ratio)
            budget = (derive_budget(replace(st.session_state.demo.state, parameters=parameters))
                      if budget_mode == DERIVED_BUDGET_MODE else override)
            st.session_state.demo = controller.restart(st.session_state.demo, parameters, budget)
            st.session_state.scenario_revision += 1
            clear_selection()
            st.session_state.active_budget_mode = budget_mode
            st.session_state.fixed_starting_budget = override
            st.session_state.settings_sync_pending = True
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    st.caption(f"Active starting capital: {st.session_state.demo.state.starting_budget:.6f} normalized units")
    st.divider()
    st.markdown("**About the baseline**")
    st.caption("Geographic distance is a travel-time proxy. Values are not calibrated minutes. "
               "Automatic mode offers two myopic one-step policies: welfare improvement per cost "
               "and maximum immediate welfare.")
    st.caption("Welfare uses driving-time-equivalent utility units. Compare changes within the active model; "
               "absolute welfare levels across models have different normalizations.")
    st.caption("Transfers are zero. Demand is known commuting demand. Runs live in this browser session.")


def agency_controls(session, state, evaluation, projects, recommendation):
    selected = None
    st.subheader("Agency")
    st.radio("Control mode", ["Manual", "Automatic"], horizontal=True,
             key="control_mode", on_change=change_mode)
    session = st.session_state.demo
    if session.mode == "Manual":
        if projects:
            ordered = sorted(projects, key=lambda p: (not p.affordable, -p.score, p.action.key))
            choices = {p.action.label: p for p in ordered}
            current = st.session_state.manual_selection
            if current.notice:
                st.info(current.notice)
            if current.edge:
                st.markdown("**Selected connection**")
                st.write(" ↔ ".join(current.edge))
                buttons = st.columns(2)
                for column, mode in zip(buttons, (Mode.BUS, Mode.RAIL)):
                    reason = mode_unavailable_reason(state, current.edge, mode)
                    column.button(mode.value.title(), key=f"construction_{mode.value}",
                                  disabled=bool(reason), help=reason or "Preview this mode before building.",
                                  type="primary" if current.mode == mode else "secondary", width="stretch",
                                  on_click=choose_construction_mode, args=(mode,))
                    if reason:
                        st.caption(f"{mode.value.title()}: {reason}")
                preview = choices.get(current.action.label)
            else:
                preview = None
                st.info(f"From {current.first}: select a highlighted second location." if current.first
                        else "Click a location on the map, then a highlighted second location.")
                st.caption("Click a corridor as a shortcut. Amber options are legal but above budget.")
            if preview:
                selected = preview.action
                project_preview(preview)
            st.button("Build project", type="primary", width="stretch", disabled=not preview or not preview.affordable,
                      on_click=build_project, args=(preview.action if preview else None,))
            st.button("Clear selection", on_click=clear_selection, width="stretch", disabled=not current.first)
            with st.expander("Choose from list"):
                st.selectbox("Project", list(choices), index=None, placeholder="Select a project",
                             key=project_widget_key(), on_change=choose_from_list,
                             format_func=lambda label: label + (" · above budget" if not choices[label].affordable else ""))
        else:
            st.info("All legal projects have already been built.")
    else:
        st.selectbox("Automatic policy", list(AGENCY_POLICIES), key="agency_policy",
                     on_change=change_agency_policy)
        if active_agency_policy() is choose_max_immediate_welfare_action:
            st.caption("Ranks affordable projects by immediate welfare improvement ΔW without dividing by construction cost.")
        else:
            st.caption("Ranks affordable projects by immediate welfare improvement ΔW per unit construction cost.")
        st.caption("Both policies are one-step/myopic; they do not plan future stages.")
        if recommendation:
            selected = recommendation.action
            project_preview(recommendation)
        else:
            st.info(controller.NO_BUILD_MESSAGE)
        a, b = st.columns(2)
        a.button("Step", on_click=advance_once, disabled=recommendation is None, width="stretch")
        b.button("Pause" if session.playing else "Play", on_click=toggle_playback,
                 disabled=recommendation is None and not session.playing, type="primary", width="stretch")
        st.caption("One construction action every two seconds during playback.")
    if session.message:
        st.info(session.message)

    return selected


@st.dialog("Explore the network", width="large", on_dismiss=close_explore)
def explore_network():
    if st.session_state.pop("inspector_changed", False):
        st.rerun(scope="app")
    state = st.session_state.demo.state
    evaluation = evaluate_network(state)
    projects = score_actions(state)
    st.session_state.setdefault("_od_index", st.session_state.od_index)
    st.session_state.setdefault("_highlight_route", st.session_state.highlight_route)
    if st.button("Close Explore", key="close_explore"):
        close_explore()
        st.rerun(scope="app")
    od_tab, flow_tab, history_tab, projects_tab = st.tabs(["Traveler response", "Corridor flows", "Action history", "Project ranking"])
    with od_tab:
        st.selectbox("Inspect commuting OD", range(len(evaluation.travelers)), key="_od_index", on_change=commit_inspector,
                     format_func=lambda i: f"{evaluation.travelers[i].demand.origin} → {evaluation.travelers[i].demand.destination}")
        st.toggle("Highlight inspected transit route", key="_highlight_route", on_change=commit_inspector)
        outcome = evaluation.travelers[st.session_state.od_index]
        cells = st.columns(4)
        cells[0].metric("Commuting demand", f"{outcome.demand.count:,.0f}")
        cells[1].metric("OD transit share", f"{outcome.transit_share:.1%}")
        cells[2].metric("Transit-time proxy", f"{outcome.route.time:.3f}" if isfinite(outcome.route.time) else "No path")
        cells[3].metric("Driving-time proxy", f"{outcome.driving_time:.3f}")
        st.write(f"Transit: {outcome.transit_count:,.1f} · Driving: {outcome.driving_count:,.1f}")
        st.caption(f"OD welfare: {outcome.welfare:,.2f} driving-time-equivalent utility units.")
        if outcome.response.types:
            st.dataframe([{"Type": t.name, "Population mass": outcome.demand.count * t.population_share,
                           "Tolerance r": t.tolerance_ratio, "Chosen mode": "Transit" if t.chooses_transit else "Driving",
                           "Transit generalized cost": f"{t.transit_cost:.3f}" if isfinite(t.transit_cost) else "Unavailable",
                           "Driving generalized cost": t.driving_cost, "Chosen utility": t.utility}
                          for t in outcome.response.types], hide_index=True, width="stretch",
                         column_config={"Population mass": st.column_config.NumberColumn(format="%.1f"),
                                        "Tolerance r": st.column_config.NumberColumn(format="%.2f"),
                                        "Driving generalized cost": st.column_config.NumberColumn(format="%.3f"),
                                        "Chosen utility": st.column_config.NumberColumn(format="%.3f")})
        if outcome.route.nodes:
            st.write(" → ".join(outcome.route.nodes))
            st.caption(" · ".join(f"{a} → {b}: {mode.value}" for a, b, mode in
                                   zip(outcome.route.nodes, outcome.route.nodes[1:], outcome.route.modes)))
        else:
            st.info("No transit path is available; all demand uses the driving outside option.")
    with flow_tab:
        st.caption("Fractional commuting mass assigned to the selected shortest transit paths; corridor flows can share riders.")
        st.dataframe(corridor_rows(state, evaluation), hide_index=True, width="stretch",
                     column_config={name: st.column_config.NumberColumn(format="%.3f")
                                    for name in ("Length proxy (km)", "Transit flow", "Bus flow", "Rail flow")})
    with history_tab:
        if state.history:
            st.dataframe([{"Stage": h.stage, "Action": h.action.label, "Cost": h.cost,
                           "Welfare improvement ΔW": h.welfare_improvement, "Welfare / cost": h.score,
                           "Remaining capital": h.remaining_budget, "Welfare W": h.welfare,
                           "Actual travel-time proxy H": h.actual_time, "Actual-time change ΔH": h.actual_time_change,
                           "Transit share": h.transit_share} for h in state.history], hide_index=True, width="stretch")
        else:
            st.caption("No construction actions yet.")
    with projects_tab:
        st.caption(f"Ranking: {st.session_state.agency_policy}. Legal projects, including those above budget. "
                   "Manual mode can choose projects with nonpositive ΔW.")
        st.dataframe([{"Project": p.action.label, "Cost": p.cost, "Welfare improvement ΔW": p.welfare_improvement,
                       "Welfare / cost": p.score, "Actual-time change ΔH": p.actual_time_change, "Affordable": p.affordable,
                       "Capital after build": p.remaining_after}
                      for p in sorted(projects, key=automatic_ranking_key)], hide_index=True, width="stretch")


def main():
    st.html("<style>" + (Path(__file__).parent / "transit_wargame/fullscreen.css").read_text(encoding="utf-8") + "</style>")
    if "demo" not in st.session_state:
        try:
            scenario = load_boston_scenario()
            st.session_state.demo = controller.Session(initialize_scenario(scenario))
        except (OSError, ValueError) as exc:
            st.error(f"Cannot load the baseline scenario: {exc}")
            st.stop()
    for key, value in {"scenario_revision": 0, "map_view_revision": 0,
                       "agency_policy": "Welfare improvement per cost",
                       "active_budget_mode": FIXED_BUDGET_MODE,
                       "fixed_starting_budget": DEFAULT_STARTING_BUDGET, "explore_open": False,
                       "od_index": 0, "highlight_route": False,
                       "manual_selection": ManualSelection(), "selection_epoch": 0}.items():
        st.session_state.setdefault(key, value)
    # Keep the selected strategy when its widget is hidden in Manual mode.
    st.session_state.agency_policy = st.session_state.agency_policy
    # Retain previously applied custom capital if an older browser session reruns.
    if st.session_state.active_budget_mode == "Custom starting budget":
        st.session_state.active_budget_mode = FIXED_BUDGET_MODE
        st.session_state.fixed_starting_budget = st.session_state.demo.state.starting_budget
        st.session_state.settings_sync_pending = True
    if "traveler_model" not in st.session_state or st.session_state.pop("settings_sync_pending", False):
        sync_settings()
    session = st.session_state.demo
    state = session.state
    evaluation = evaluate_network(state)
    projects = score_actions(state)
    recommendation = active_agency_policy()(projects)

    with st.container(key="left_panel"):
        st.subheader("Boston transit wargame")
        st.caption(f"Active traveler model: {state.parameters.traveler_model.label}")
        stats_tab, scenario_tab = st.tabs(["Statistics", "Scenario"])
        with stats_tab:
            statistics(state, evaluation)
        with scenario_tab:
            scenario_settings()
        st.button("Explore", on_click=open_explore, width="stretch", icon="🔎")
    with st.container(key="right_panel"):
        selected = agency_controls(session, state, evaluation, projects, recommendation)
    with st.container(key="map_toolbar"):
        a, b = st.columns([1.4, 1])
        a.button("Recenter map", on_click=recenter_map, width="stretch")
        with b.popover("Layers", width="stretch"):
            st.toggle("Show unserved candidates", key="show_candidates")
            st.toggle("Emphasize transit flow", key="flow_widths")
            st.caption("Drag to pan · scroll to zoom. Straight corridors are model connections. "
                       "Public background tiles require internet access.")
    show_candidates = st.session_state.get("show_candidates", False)
    flow_widths = st.session_state.get("flow_widths", False)
    with st.container(key="map_legend"):
        with st.expander("Legend"):
            st.html(map_legend(show_candidates))
    route = evaluation.travelers[st.session_state.od_index].route.nodes if st.session_state.highlight_route else ()
    revision = f"boston-network:{st.session_state.scenario_revision}:{st.session_state.map_view_revision}"
    current = st.session_state.manual_selection
    options = selection_overlay(state, current, projects) if session.mode == "Manual" else ()
    render_map(map_figure(state, evaluation, show_candidates, flow_widths, selected, route, revision,
                         selection=current if session.mode == "Manual" else None, eligible=options),
               revision, interaction_payload(state, current, session.mode, selection_context()), commit_map_selection)
    if st.session_state.explore_open:
        explore_network()
    ticker = st.fragment(run_every=controller.PLAY_INTERVAL_SECONDS if session.playing else None)(playback_tick)
    ticker()


main()
