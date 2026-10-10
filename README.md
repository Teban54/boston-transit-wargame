# Boston transit wargame

An interactive, stylized 14-location Boston transit investment simulation. Build
bus or rail, observe traveler response, and compare two automatic agency policies.
The default scenario uses Discrete types and cumulative capital B=50. Runs live
in each browser session; there is no persistence or database.

**Public demo:** https://boston-transit-wargame.streamlit.app/  
**Repository:** https://github.com/Teban54/boston-transit-wargame

## Run locally

Use Python 3.12. From this repository root:

```text
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` on Windows PowerShell or
`source .venv/bin/activate` on Linux/macOS, then run:

```text
python -m pip install -r requirements.txt
python -m pip check
python -m streamlit run app.py
```

Open the local URL printed by Streamlit, normally http://localhost:8501.
Streamlit 1.54.0, Plotly 6.5.2, and pandas 2.3.3 are pinned. Plotly.js loads from
the installed Python package; no CDN, npm build, or map API key is required.
The included CSV, JavaScript, CSS, and `.streamlit/config.toml` are required assets.

## Use the demo

- **Manual:** click a location, then a highlighted eligible second location.
  Choose Bus or Rail in Agency, inspect the preview, and click Build project.
  Selecting or previewing never spends capital. Rail added to bus retains bus.
  Choose from list is a keyboard-friendly alternative.
- **Automatic:** Welfare improvement per cost ranks by ΔW/c; Maximum immediate
  welfare ranks by ΔW. Both are myopic and consider only affordable projects with
  positive welfare improvement. Step builds once; Play builds every two seconds.
  Pause stops playback, which also stops when no beneficial affordable project remains.
- **Statistics / Scenario:** the left card holds metrics, traveler models,
  parameters, fixed/derived budgets, and Reset. Explore opens traveler response,
  corridor flows, action history, and project ranking. Scenario restarts clear
  history and pause playback; Reset returns to Discrete types.
- **Map:** drag to pan, scroll to zoom, and use Recenter or Layers on the toolbar.
  Unserved candidates are dark-purple dotted lines. One persistent Plotly/MapLibre
  canvas updates overlays in place; builds and playback retain pan/zoom.
  Below 960 pixels the side cards start collapsed and open through labeled buttons.

The initial network contains 14 nodes, 9 rail corridors, 6 bus corridors, and
17 unserved candidates. Its 45 modeled OD pairs total 121,372 commuters.
Nominal Discrete-types transit share is 67.06%. With B=50, welfare per cost first
builds Back Bay–Kendall bus; maximum immediate welfare first builds
Downtown–Everett/Chelsea rail.

## Deploy to Streamlit Community Cloud

1. Publish this repository as **Teban54/boston-transit-wargame**, public,
   with branch **master**.
2. Sign in at https://share.streamlit.io and select the Teban54 workspace.
3. Choose Create app → Deploy from repo. Set repository
   `Teban54/boston-transit-wargame`, branch `master`, and main file `app.py`.
4. In Advanced settings explicitly choose **Python 3.12**. No secrets are needed.
5. Request subdomain `boston-transit-wargame`; accept the assigned URL if unavailable.
6. Deploy and inspect the build logs. Verify public access in a signed-out browser.

Cloud runs Linux and starts from the repository root. The data and asset paths
are portable. Public repositories produce public apps by default; verify the
actual sharing setting and anonymous access after deployment.

Deployment was verified on October 10, 2026: Python 3.12.15 on Cloud, public
sharing, two independent anonymous sessions, both traveler models and agency
policies, manual construction, Step/Play/Pause, and stable map canvas identity.
The original workspace passed all 130 tests in both the isolated deployment
environment and the shared environment. The shared environment emitted two
existing dependency deprecation warnings. Cloud applied its own pyarrow 24
compatibility replacement while retaining the three pinned direct dependencies.

Community Cloud currently sleeps apps after 12 hours without traffic. Open and
verify the app before presenting. Repository pushes update the connected app;
dependency changes trigger reinstallation. See the official
[deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy),
[sharing](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app), and
[management](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app) docs.

## Update from the research workspace

This is an allowlisted deployment mirror. Research documents, transcripts,
workbooks, QA images, tests, secrets, and local environments are not published.
The original research workspace remains outside Git. From that workspace:

```text
python tools/prepare_streamlit_deploy.py
python tools/prepare_streamlit_deploy.py --check
python -m pytest -q
```

The exporter copies runtime files byte-for-byte, generates this README and the
deployment dependency/ignore files, and preserves `.git` and unrelated files.
If the package gains a runtime file, update the exporter's explicit allowlist.
Review the deployment repository's diff and staged files before each push.
Commit and push from the deployment directory:

```text
git add app.py transit_wargame data .streamlit/config.toml requirements.txt README.md .gitignore
git commit -m "Update transit wargame"
git push origin master
```

Repeat clean-environment smoke checks when dependencies change. The full
regression suite belongs to the original research workspace because it uses
unpublished model-source fixtures; `python -m pytest -q` is not an exported-repo
command.

## Validation checklist

Check launch defaults, both traveler models, manual Bus/Rail construction,
both automatic policies, Step/Play/Pause, budget application/reset, Explore,
candidate overlays, and separate welfare/actual-time displays. Verify tiles and
attribution, desktop/narrow layouts, click/hover behavior, stable map canvas and
pan/zoom across actions, anonymous access, and independent viewer sessions.

## Model limits and map attribution

Travel times and corridor lengths use great-circle geographic proxies, not
calibrated minutes. Capital uses normalized cost units, not dollars. Welfare
uses driving-time-equivalent utility units; compare changes within the active
model rather than absolute levels across models. Parameters are temporary.

The light basemap uses public CARTO Positron tiles with OpenStreetMap data and
requires internet access. CARTO/OpenStreetMap attribution remains visible at
bottom right. Straight edges represent model corridors, not real alignments.

No learning, attack/defense, capacity, congestion, operating costs, construction
delays, richer transfers, rolling capital, or non-myopic planning is implemented.
