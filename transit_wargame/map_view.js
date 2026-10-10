// Plotly is supplied once by map_view.py from the installed package, without a CDN.
// v2 invokes this renderer on data updates; resources belong to the mounted host.
const instances = new WeakMap();

export default function ({ parentElement, data, setTriggerValue }) {
  let state = instances.get(parentElement);
  if (!state) {
    const host = parentElement.querySelector(".transit-map-host");
    const graph = document.createElement("div");
    graph.className = "transit-map-graph";
    graph.dataset.mapInstance = crypto.randomUUID();
    graph.dataset.initializations = "0";
    graph.dataset.updates = "0";
    host.appendChild(graph);
    const narrow = window.matchMedia("(max-width: 959px)");
    state = { graph, host, initialized: false, disposed: false, running: false,
              pending: null, signature: null, revision: null, camera: null,
              panels: { left: !narrow.matches, right: !narrow.matches }, buttons: {} };
    instances.set(parentElement, state);
    state.help = document.createElement("div");
    state.help.className = "transit-map-help";
    state.help.setAttribute("role", "status");
    state.help.setAttribute("aria-live", "polite");
    document.body.appendChild(state.help);

    // Collapse is browser-only presentation state. Native inputs remain mounted,
    // so parameter drafts, playback, and selected projects survive navigation.
    for (const [side, label] of [["left", "Statistics & scenario"], ["right", "Agency"]]) {
      const button = document.createElement("button");
      button.className = `transit-panel-toggle ${side}`;
      button.addEventListener("click", () => {
        state.panels[side] = !state.panels[side];
        if (narrow.matches && state.panels[side]) state.panels[side === "left" ? "right" : "left"] = false;
        updatePanels();
      });
      document.body.appendChild(button);
      state.buttons[side] = { button, label };
    }
    const updatePanels = () => {
      for (const side of ["left", "right"]) {
        document.body.classList.toggle(`transit-${side}-closed`, !state.panels[side]);
        const { button, label } = state.buttons[side];
        button.textContent = state.panels[side] ? "×" : label;
        button.setAttribute("aria-label", `${state.panels[side] ? "Collapse" : "Open"} ${label}`);
        button.setAttribute("aria-expanded", String(state.panels[side]));
        button.title = button.getAttribute("aria-label");
      }
    };
    state.mediaChange = () => {
      state.panels = { left: !narrow.matches, right: !narrow.matches };
      updatePanels();
    };
    narrow.addEventListener("change", state.mediaChange);
    updatePanels();
    state.showAgency = () => {
      state.panels.right = true;
      if (narrow.matches) state.panels.left = false;
      updatePanels();
    };
    state.emitSelection = (value) => {
      if (!state.interaction?.enabled || state.disposed || state.running) return;
      state.trigger?.("selection", {...value, context: state.interaction.context});
    };
    state.onClick = (event) => {
      const target = event.points?.find(p => p.customdata?.kind === "node")?.customdata
        || event.points?.find(p => p.customdata?.kind === "corridor")?.customdata;
      if (target) state.emitSelection(target);
    };
    state.onKey = (event) => {
      if (event.key !== "Escape" || event.defaultPrevented || document.querySelector('[role="dialog"]')) return;
      if (event.target?.closest?.('input, textarea, select, [role="combobox"], [role="listbox"]')) return;
      if (state.interaction?.first) state.emitSelection({kind: "clear"});
    };
    document.addEventListener("keydown", state.onKey);
    state.resize = new ResizeObserver(() => {
      if (state.initialized && !state.running && !state.disposed) Plotly.Plots.resize(graph);
      else state.resizePending = true;
    });
    state.resize.observe(host);
    state.onRelayout = (event) => {
      if (!Object.keys(event).some(key => key.startsWith("map."))) return;
      const map = graph.layout?.map;
      if (map?.center) state.camera = {
        center: { ...map.center }, zoom: map.zoom, bearing: map.bearing, pitch: map.pitch,
      };
      // No setStateValue: map gestures never trigger a Python rerun.
    };
    state.drain = async () => {
      if (state.running || state.disposed) return;
      state.running = true;
      try {
        while (state.pending && !state.disposed) {
          const update = state.pending;
          state.pending = null;
          if (update.signature === state.signature) continue;
          const figure = structuredClone(update.figure);
          const reset = update.revision !== state.revision;
          if (!reset && state.camera) Object.assign(figure.layout.map, state.camera);
          if (reset) state.camera = null;
          figure.layout.autosize = true;
          delete figure.layout.height;
          delete figure.layout.width;
          const config = { displayModeBar: false, scrollZoom: true, responsive: true };
          if (!state.initialized) {
            await Plotly.newPlot(graph, figure.data, figure.layout, config);
            state.initialized = true;
            if (state.disposed) break;
            graph.dataset.initializations = "1";
            const canvas = graph.querySelector(".maplibregl-canvas");
            if (canvas) canvas.dataset.mapCanvasInstance = crypto.randomUUID();
            graph.on("plotly_relayout", state.onRelayout);
            graph.on("plotly_click", state.onClick);
          } else {
            await Plotly.react(graph, figure.data, figure.layout, config);
            if (state.disposed) break;
          }
          state.signature = update.signature;
          delete graph.dataset.mapError;
          state.revision = update.revision;
          graph.dataset.viewRevision = update.revision;
          graph.dataset.updates = String(Number(graph.dataset.updates) + 1);
        }
      } catch (error) {
        console.error("Transit map update failed", error);
        graph.dataset.mapError = String(error);
      } finally {
        state.running = false;
        if (state.disposed && state.initialized) Plotly.purge(graph);
        else if (state.pending) state.drain();
        else if (state.resizePending && state.initialized) {
          state.resizePending = false;
          Plotly.Plots.resize(graph);
        }
      }
    };
    state.dispose = () => {
      state.disposed = true;
      state.pending = null;
      state.resize.disconnect();
      narrow.removeEventListener("change", state.mediaChange);
      graph.removeListener?.("plotly_relayout", state.onRelayout);
      graph.removeListener?.("plotly_click", state.onClick);
      document.removeEventListener("keydown", state.onKey);
      state.help.remove();
      if (state.initialized && !state.running) Plotly.purge(graph);
      for (const side of ["left", "right"]) {
        state.buttons[side].button.remove();
        document.body.classList.remove(`transit-${side}-closed`);
      }
      instances.delete(parentElement);
    };
  }
  state.trigger = setTriggerValue;
  state.interaction = data.interaction;
  state.help.textContent = data.interaction?.status || "";
  state.help.hidden = !data.interaction;
  const pair = data.interaction?.second ? JSON.stringify([
    data.interaction.context, data.interaction.first, data.interaction.second]) : null;
  if (pair && pair !== state.shownPair) state.showAgency();
  state.shownPair = pair;
  state.pending = data;
  state.drain();
  // Streamlit v2 calls cleanup on actual unmount, not ordinary data updates.
  return state.dispose;
}
