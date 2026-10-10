"""Pure presentation transitions; rendering itself never commits an action."""

from dataclasses import dataclass, replace

from .domain import Action, AgencyPolicy, Parameters, State, TravelerModel
from .engine import apply_action, choose_welfare_per_cost_action, initialize_scenario, score_actions

PLAY_INTERVAL_SECONDS = 2.0
NO_BUILD_MESSAGE = "No affordable project has positive welfare improvement. No build."


@dataclass(frozen=True)
class Session:
    state: State
    mode: str = "Manual"
    playing: bool = False
    next_tick: float | None = None
    message: str = ""


def pause(session: Session) -> Session:
    return replace(session, playing=False, next_tick=None)


def switch_mode(session: Session, mode: str) -> Session:
    if mode not in ("Manual", "Automatic"):
        raise ValueError("Unknown control mode.")
    return replace(pause(session), mode=mode, message="")


def play(session: Session, now: float, policy: AgencyPolicy = choose_welfare_per_cost_action) -> Session:
    if session.mode != "Automatic":
        raise ValueError("Playback requires automatic mode.")
    if policy(score_actions(session.state)) is None:
        return replace(pause(session), message=NO_BUILD_MESSAGE)
    return replace(session, playing=True, next_tick=now + PLAY_INTERVAL_SECONDS, message="")


def step(session: Session, policy: AgencyPolicy = choose_welfare_per_cost_action) -> Session:
    choice = policy(score_actions(session.state))
    if choice is None:
        return replace(pause(session), message=NO_BUILD_MESSAGE)
    state = apply_action(session.state, choice.action)
    return replace(session, state=state, message=f"Built {choice.action.label}.")


def tick(session: Session, now: float, policy: AgencyPolicy = choose_welfare_per_cost_action) -> Session:
    if not session.playing or session.next_tick is None or now < session.next_tick:
        return session
    advanced = step(session, policy)
    if not advanced.playing:
        return advanced
    if policy(score_actions(advanced.state)) is None:
        return replace(pause(advanced), message=f"{advanced.message} {NO_BUILD_MESSAGE}")
    # Schedule from completion, never catch up with multiple projects in one tick.
    return replace(advanced, next_tick=now + PLAY_INTERVAL_SECONDS)


def manual_build(session: Session, action: Action) -> Session:
    return replace(pause(session), state=apply_action(session.state, action),
                   message=f"Built {action.label}.")


def restart(session: Session, parameters: Parameters | None = None,
            starting_budget: float | None = None) -> Session:
    """Keep applied capital unless given a new fixed or explicitly derived value."""
    # Reset returns to discrete types; explicit parameter application honors its model.
    budget = session.state.starting_budget if starting_budget is None else starting_budget
    parameters = parameters or replace(session.state.parameters, traveler_model=TravelerModel.DISCRETE_TYPES)
    state = initialize_scenario(session.state.scenario, parameters, budget)
    return Session(state, mode=session.mode, message="Scenario restarted from the canonical network.")


def switch_traveler_model(session: Session, model: TravelerModel | str) -> Session:
    parameters = replace(session.state.parameters, traveler_model=TravelerModel(model))
    return restart(session, parameters, session.state.starting_budget)
