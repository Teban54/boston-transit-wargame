"""Persistent browser-owned Plotly map, using the locally installed JS bundle."""

from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path

import streamlit as st
from plotly.graph_objects import Figure
from plotly.offline import get_plotlyjs

MAP_HTML = ('<div class="transit-map-host" aria-label="Interactive Boston transit map"></div>'
            '<div class="transit-map-attribution">© '
            '<a href="https://carto.com/about-carto/" target="_blank" rel="noopener noreferrer">CARTO</a>, © '
            '<a href="https://www.openstreetmap.org/about/" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>'
            ' contributors</div>')
@lru_cache(maxsize=1)
def component_script() -> str:
    # Component modules are cached by Streamlit. The installed MIT-licensed bundle
    # runs once in module scope, independently of per-update renderer calls.
    return ("const Plotly = (() => { const module = {exports: {}};\n"
            + get_plotlyjs() + "\nreturn module.exports; })();\n"
            + Path(__file__).with_suffix(".js").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def map_component():
    return st.components.v2.component(
        "transit_map_view", js=component_script(), isolate_styles=False,
        html=MAP_HTML,
    )


def map_payload(figure: Figure, revision: str) -> dict:
    """Stable JSON/signature; camera is retained only in the browser."""
    figure_json = figure.to_json(remove_uids=False)
    return {"figure": json.loads(figure_json), "revision": revision,
            "signature": sha256(figure_json.encode("utf-8")).hexdigest()}


def render_map(figure: Figure, revision: str, interaction: dict | None = None,
               on_selection_change=None) -> None:
    payload = map_payload(figure, revision)
    payload["interaction"] = interaction
    map_component()(data=payload, key="network_map", height=0,
                    on_selection_change=on_selection_change)
