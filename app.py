"""Continuity Checker - editor app (Streamlit).

    streamlit run app.py

Flow: 1 Load scripts -> 2 Characters -> 3 Run check -> 4 Review -> 5 Download
API keys come from the project's .env file (see .env.example); the app never asks for them.
Colours and fonts live in .streamlit/config.toml; the extra styling is in CSS below.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import time
from pathlib import Path

import streamlit as st

from cc.aliases import resolve_aliases
from cc.bible import Bible
from cc.llm import LLM, wait_reason
from cc.parsing import load_sources, load_uploaded_zip, parse_sources
from cc.pipeline import ROOT, load_config, load_profile, rules_only, run
from cc.report import ranked_flags

st.set_page_config(page_title="Continuity Checker", page_icon="🎬", layout="wide")

# Streamlit Community Cloud: there is no .env file there, so API keys come from the app's Secrets settings.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str) and _v and not os.environ.get(_k):
            os.environ[_k] = _v
except Exception:
    pass  # no secrets configured (normal when running locally with .env)
ON_CLOUD = Path("/mount/src").exists()   # where Streamlit Community Cloud places the app's code
RUNS = ROOT / "app_runs"
CACHE = ROOT / ".cache" / "llm_cache.sqlite"
DEMOS = {
    "contract_heiress": ("The Contract Heiress", "billionaire_romance", "CH",
                         "A billionaire romance with a few planted mistakes - and some tricky twists that are not mistakes."),
    "river_witch": ("The River Witch", "fantasy", "RW",
                    "A fantasy show with Hindi-English dialogue, written in screenplay format."),
}
GENRES = sorted(p.stem for p in (ROOT / "profiles").glob("*.yaml"))
STEPS = ["load", "names", "check", "review", "download"]
STEP_LABEL = {"load": ":material/upload_file: Load scripts", "names": ":material/groups: Characters",
              "check": ":material/play_circle: Run check", "review": ":material/fact_check: Review",
              "download": ":material/download: Download"}

# Plain-language labels
KIND = {"value_change": "Detail changes", "relationship_inverse": "Relationship mismatch",
        "speaks_after_death": "Speaks after dying", "learns_twice": "Learns a secret twice",
        "notes_mismatch": "Notes don't match script", "possible_wrong_merge": "Names may be wrongly joined"}
VERDICT = {"contradiction": "Likely mistake", "unsure": "Needs a look", "story_change": "Story change",
           "intentional": "On purpose", "extraction_error": "Misread by the checker"}
SEVERITY = {"high": "Serious", "medium": "Medium", "low": "Minor"}
STATUS = {"open": "To review", "accepted": "Real mistake", "dismissed": "Not a problem", "intentional": "On purpose"}
ss = st.session_state

# ------------------------------------------------------------------------------------------ styling
RED, RED_DARK, SMOKE = "#E7473C", "#C2342A", "#F0F0F0"
st.markdown(f"""
<style>
/* page */
.stApp {{background: radial-gradient(1100px 420px at 75% -120px, rgba(231,71,60,.13), transparent 70%),
                     radial-gradient(900px 380px at -10% 0%, rgba(231,71,60,.06), transparent 70%), {SMOKE};}}
header[data-testid="stHeader"] {{background: transparent;}}
[data-testid="stMainBlockContainer"] {{padding-top: 2.2rem; max-width: 1180px;}}
[data-testid="stSidebar"] {{box-shadow: 6px 0 30px rgba(20,10,10,.04);}}
[data-testid="stSidebarUserContent"] {{padding-top: .5rem;}}

/* brand */
.brand {{display:flex; align-items:center; gap:.7rem; margin-bottom: 1.2rem;}}
.logo {{width:40px; height:40px; border-radius:11px; display:grid; place-items:center; color:#fff; font-weight:800;
        font-size:.95rem; letter-spacing:.02em; background: linear-gradient(135deg, #F26B5F 0%, {RED} 45%, {RED_DARK} 100%);
        box-shadow: 0 6px 16px rgba(231,71,60,.35);}}
.brand-name {{font-weight:800; font-size:1.05rem; line-height:1.1; color:#1F1D1D;}}
.brand-tag {{font-size:.78rem; color:#7A7474;}}

/* hero */
.hero {{position:relative; overflow:hidden; border-radius:22px; padding:2.4rem 2.6rem; margin-bottom:1.4rem; color:#fff;
        display:flex; gap:2rem; align-items:center; justify-content:space-between;
        background: radial-gradient(600px 300px at 90% -10%, rgba(255,255,255,.28), transparent 60%),
                    radial-gradient(500px 260px at 0% 120%, rgba(240,240,240,.30), transparent 60%),
                    linear-gradient(135deg, #F06A5E 0%, {RED} 40%, {RED_DARK} 75%, #9E2219 100%);
        box-shadow: 0 20px 50px rgba(231,71,60,.28);}}
.hero .eyebrow {{color: rgba(255,255,255,.85);}}
.hero-title {{font-family:"Plus Jakarta Sans", sans-serif; font-weight:800; font-size:2.4rem; line-height:1.12;
              letter-spacing:-.02em; margin:.35rem 0 .7rem 0;}}
.hero-sub {{font-size:1.05rem; opacity:.93; max-width:560px; margin:0 0 1.1rem 0;}}
.hero-points {{display:flex; flex-wrap:wrap; gap:.5rem;}}
.hero-points span {{background: rgba(255,255,255,.16); border:1px solid rgba(255,255,255,.28); border-radius:999px;
                    padding:.3rem .8rem; font-size:.85rem; backdrop-filter: blur(4px);}}
.hero-card {{flex:0 0 330px; background:#fff; color:#1F1D1D; border-radius:16px; padding:1.1rem 1.2rem;
             box-shadow: 0 18px 40px rgba(60,10,10,.25); transform: rotate(1.2deg);}}
.hero-card .hc-top {{display:flex; justify-content:space-between; align-items:center; margin-bottom:.6rem;}}
.hero-card .hc-title {{font-weight:700; font-size:.98rem; margin-bottom:.55rem;}}
.hero-card .hc-line {{font-size:.86rem; background:{SMOKE}; border-left:3px solid {RED}; border-radius:0 8px 8px 0;
                      padding:.4rem .6rem; margin:.35rem 0;}}
.hero-card .hc-line small {{display:block; color:#8A8484; font-size:.72rem; font-weight:600; letter-spacing:.04em;}}
@media (max-width: 900px) {{ .hero {{flex-direction:column; padding:1.8rem;}} .hero-card {{flex:auto; width:100%; transform:none;}}
                             .hero-title {{font-size:1.8rem;}} }}

/* page headings */
.eyebrow {{font-size:.74rem; font-weight:700; letter-spacing:.12em; text-transform:uppercase; color:{RED};}}
.ph {{margin: 1.6rem 0 1rem 0;}}
.ph-title {{font-family:"Plus Jakarta Sans", sans-serif; font-weight:800; font-size:1.9rem; letter-spacing:-.02em;
            color:#1F1D1D; margin:.2rem 0 .25rem 0;}}
.ph-sub {{color:#6E6868; font-size:1rem; margin:0; max-width:760px;}}
.sec {{font-family:"Plus Jakarta Sans", sans-serif; font-weight:700; font-size:1.15rem; margin:1.2rem 0 .4rem 0;}}

/* step bar */
.st-key-stepbar {{background:#fff; border-radius:14px; padding:6px; box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 6px 20px rgba(0,0,0,.04);}}
.st-key-stepbar [data-testid="stButtonGroup"] > div {{width:100%; gap:6px;}}
.st-key-stepbar button {{flex:1 1 0; border:none !important; border-radius:10px !important; padding:.65rem .8rem;
                         background:transparent; color:#5E5858; font-weight:600; transition: all .15s ease;}}
.st-key-stepbar button:hover {{background:{SMOKE}; color:{RED};}}
.st-key-stepbar button[data-testid="stBaseButton-segmented_controlActive"] {{color:#fff;
        background: linear-gradient(135deg, #F06A5E 0%, {RED} 50%, {RED_DARK} 100%);
        box-shadow: 0 6px 16px rgba(231,71,60,.32);}}

/* cards */
[class*="st-key-card"] {{background:#fff; border:1px solid #E9E6E6; border-radius:16px; padding:1.25rem 1.35rem;
                         box-shadow: 0 1px 2px rgba(20,10,10,.04), 0 10px 28px rgba(20,10,10,.05);
                         transition: box-shadow .2s ease, transform .2s ease;}}
[class*="st-key-card"]:hover {{box-shadow: 0 1px 2px rgba(20,10,10,.05), 0 16px 36px rgba(20,10,10,.08);}}
[class*="st-key-card_sev_high"] {{border-left:5px solid {RED};}}
[class*="st-key-card_sev_medium"] {{border-left:5px solid #F2A33A;}}
[class*="st-key-card_sev_low"] {{border-left:5px solid #A3A9B5;}}
.tile {{width:46px; height:46px; border-radius:12px; display:grid; place-items:center; font-weight:800; color:#fff;
        background: linear-gradient(135deg, #F06A5E, {RED_DARK}); box-shadow: 0 6px 14px rgba(231,71,60,.28);
        margin-bottom:.7rem;}}
.tile.soft {{background: rgba(231,71,60,.10); color:{RED}; box-shadow:none;}}
.mi {{font-family:"Material Symbols Rounded"; font-weight:normal; font-style:normal; font-size:24px; line-height:1;
      letter-spacing:normal; text-transform:none; white-space:nowrap; font-feature-settings:"liga"; -webkit-font-smoothing:antialiased;}}
.card-title {{font-family:"Plus Jakarta Sans", sans-serif; font-weight:700; font-size:1.15rem; margin:0 0 .25rem 0;}}
.card-text {{color:#6E6868; font-size:.92rem; margin:0 0 .4rem 0;}}

/* stats */
[data-testid="stMetric"] {{background:#fff; border-radius:16px; padding:1rem 1.2rem 1.05rem 1.2rem; position:relative;
                           border:1px solid #E9E6E6; box-shadow: 0 1px 2px rgba(20,10,10,.04), 0 8px 22px rgba(20,10,10,.04);
                           overflow:hidden;}}
[data-testid="stMetric"]::before {{content:""; position:absolute; left:0; top:0; right:0; height:3px;
                                   background: linear-gradient(90deg, {RED}, #F5A49D 70%, {SMOKE});}}
[data-testid="stMetricLabel"] p {{font-size:.78rem !important; font-weight:600; letter-spacing:.06em;
                                  text-transform:uppercase; color:#857F7F;}}
[data-testid="stMetricValue"] {{font-family:"Plus Jakarta Sans", sans-serif; font-weight:800; font-size:2rem; color:#1F1D1D;}}

/* buttons */
button[data-testid="stBaseButton-primary"] {{border:none; font-weight:600; letter-spacing:.01em;
        background: linear-gradient(135deg, #F06A5E 0%, {RED} 50%, {RED_DARK} 100%);
        box-shadow: 0 8px 20px rgba(231,71,60,.28); transition: transform .15s ease, box-shadow .15s ease;}}
button[data-testid="stBaseButton-primary"]:hover {{transform: translateY(-1px); box-shadow: 0 12px 26px rgba(231,71,60,.36);}}
button[data-testid="stBaseButton-secondary"] {{background:#fff; font-weight:500; transition: all .15s ease;}}
button[data-testid="stBaseButton-secondary"]:hover {{border-color:{RED}; color:{RED}; background:#FFF7F6;}}

/* problem cards */
.flag-title {{font-family:"Plus Jakarta Sans", sans-serif; font-weight:700; font-size:1.12rem; margin:.15rem 0 .5rem 0;}}
.badges {{display:flex; flex-wrap:wrap; gap:.4rem; margin-bottom:.9rem;}}
.badge {{display:inline-flex; align-items:center; gap:.35rem; padding:.2rem .65rem; border-radius:999px;
         font-size:.76rem; font-weight:600;}}
.badge.dot::before {{content:""; width:6px; height:6px; border-radius:50%; background:currentColor;}}
.b-high {{background:rgba(231,71,60,.12); color:#B8291F;}}
.b-medium {{background:#FFF1DC; color:#9A5800;}}
.b-low {{background:#EEF0F4; color:#4A5568;}}
.b-kind {{background:{SMOKE}; color:#3A3737;}}
.b-verdict {{border:1px solid #E2E0E0; color:#6B6767;}}
.b-done {{background:#E7F6EE; color:#1E7A46;}}
.side-label {{font-size:.74rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:#8A8484;}}
.side-where {{font-weight:600; margin:.1rem 0 .4rem 0;}}
.quote {{background:{SMOKE}; border-left:3px solid {RED}; border-radius:0 12px 12px 0; padding:.7rem .9rem;
         font-size:1rem; line-height:1.5; margin:0 0 .5rem 0;}}
.why {{background: linear-gradient(90deg, rgba(231,71,60,.07), rgba(240,240,240,0)); border-radius:10px;
       padding:.6rem .8rem; font-size:.93rem; margin:.4rem 0 .8rem 0;}}
.ctx {{font-size:.88rem; color:#6E6868; margin:.15rem 0;}}
.ctx.hit {{color:#1F1D1D; font-weight:500;}}
.ctx b {{color:#3A3737;}}

/* name pairs */
.pair {{display:flex; align-items:center; gap:.6rem; font-family:"Plus Jakarta Sans", sans-serif; font-weight:700;
        font-size:1.12rem; margin-bottom:.3rem;}}
.pair .vs {{width:30px; height:30px; border-radius:50%; display:grid; place-items:center; font-size:.9rem;
            background: rgba(231,71,60,.10); color:{RED};}}

/* empty states & footer */
.empty {{text-align:center; padding:2.6rem 1rem; background:#fff; border:1px dashed #DAD6D6; border-radius:16px; margin:.5rem 0 1rem 0;}}
.empty .tile {{margin: 0 auto .8rem auto;}}
.empty-title {{font-weight:700; font-size:1.1rem;}}
.empty-text {{color:#6E6868; font-size:.93rem;}}
.footer {{text-align:center; color:#9A9494; font-size:.8rem; margin-top:3rem; padding-top:1.2rem; border-top:1px solid #E2E0E0;}}
.side-card {{background:{SMOKE}; border-radius:12px; padding:.8rem .9rem; margin-bottom:.6rem;}}
.side-card .t {{font-weight:700;}} .side-card .s {{font-size:.82rem; color:#7A7474;}}
.side-h {{font-size:.72rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:#8A8484; margin:.9rem 0 .4rem 0;}}
[data-testid="stExpander"] details {{background:#fff; border-radius:14px; border-color:#E9E6E6;}}
[data-testid="stExpander"] summary:hover {{color:{RED};}}
[data-testid="stProgress"] > div > div > div > div {{background: linear-gradient(90deg, {RED}, #F5847A) !important;}}
</style>
""", unsafe_allow_html=True)


def html_block(s: str):
    """Render an HTML snippet (lines are de-indented so markdown doesn't treat them as code)."""
    st.markdown(" ".join(line.strip() for line in s.splitlines()), unsafe_allow_html=True)


def esc(s) -> str:
    return html.escape(str(s or ""))


# ------------------------------------------------------------------------------------------ helpers
def missing_keys() -> list[str]:
    """Names of API keys the AI check needs but that aren't set (in .env or the environment)."""
    cfg = load_config()
    providers, need = cfg.get("providers", {}), set()
    for sc in cfg.get("stages", {}).values():
        env = providers.get(sc.get("provider"), {}).get("api_key_env")
        if env and not os.environ.get(env):
            need.add(env)
    return sorted(need)


def use_ai() -> bool:
    return ss.get("check_type", "").startswith("Smart")


def current_cfg() -> dict:
    cfg = load_config()
    return cfg if use_ai() else rules_only(cfg)


def out_dir() -> Path:
    return RUNS / re.sub(r"[^A-Za-z0-9_-]+", "_", ss.get("show_name", "show"))[:60]


def nice(name: str) -> str:
    return name.replace("_", " ").capitalize()


def go(step: str):
    ss["step"] = step


def keep_step():
    if ss.get("step") is None:  # clicking the selected step again would clear it
        ss["step"] = ss.get("_last_step", STEPS[0])


def next_button(step: str, label: str, icon: str = ":material/arrow_forward:"):
    st.button(label, type="primary", on_click=go, args=(step,), icon=icon)


def page_head(n: int, title: str, sub: str = ""):
    html_block(f"""<div class="ph"><div class="eyebrow">Step {n} of 5</div>
                   <div class="ph-title">{title}</div>{f'<p class="ph-sub">{sub}</p>' if sub else ''}</div>""")


def empty(icon: str, title: str, text: str, step: str, button: str):
    html_block(f"""<div class="empty"><div class="tile soft"><span class="mi">{icon}</span></div>
                   <div class="empty-title">{title}</div><div class="empty-text">{text}</div></div>""")
    next_button(step, button, icon=":material/arrow_back:")
    st.stop()


def badge(text: str, cls: str, dot: bool = False) -> str:
    return f"<span class='badge {cls}{' dot' if dot else ''}'>{esc(text)}</span>"


def show_lines(b: Bible, line_id: str):
    for r in b.context(line_id):
        who = esc(r["speaker"] or r["type"].capitalize())
        hit = " hit" if r["line_id"] == line_id else ""
        html_block(f"<p class='ctx{hit}'><b>{who}:</b> {esc(r['text'][:180])}{' ◀' if hit else ''}</p>")


def side(b: Bible, label: str, x: dict):
    who = f" · {esc(x['speaker'])}" if x.get("speaker") else ""
    html_block(f"""<div class="side-label">{label}</div><div class="side-where">Episode {x['episode']}{who}</div>
                   <div class="quote">“{esc(x['quote'])}”</div>""")
    with st.expander("Show the lines around it", icon=":material/subject:"):
        show_lines(b, x["line_id"])


PROVIDER = {"groq": "Groq", "gemini": "Gemini", "openrouter": "OpenRouter", "ollama": "The local AI"}
STAGE_DOING = {"extract": "reading the scripts", "entail": "double-checking facts", "judge": "judging problems",
               "alias": "comparing names", "notes": "checking episode notes", "parse": "reading the script layout"}


def fmt_time(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m} min {s:02d}s" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def estimate_minutes() -> tuple[int, int]:
    """Rough time for a smart check. The free Groq plan reads about 8,000 tokens a minute, and a show's
    readable text (dialogue + action, ~4 characters per token) is sent with instructions and answers on top.
    Calibrated so a 50-episode, ~70k-token show comes out at roughly 15-30 minutes."""
    pr = ss.get("parse")
    if not pr:
        return 0, 0
    tokens = sum(len(l.text) for l in pr.all_lines() if l.type != "camera") / 4
    mid = tokens / 2800
    return max(1, round(mid * 0.6)), max(2, round(mid * 1.3))


def wait_message(e: dict) -> tuple[str, str]:
    """(kind, plain-language message) for a wait reported by the AI connection."""
    who = PROVIDER.get(e["provider"], e["provider"].capitalize())
    doing = STAGE_DOING.get(e["stage"], "working")
    secs, tries = fmt_time(e["seconds"]), f"try {e['attempt']} of {e['attempts']}"
    if e["reason"] == "daily_limit":
        return "error", (f"**{who}'s free daily limit is used up**, so {doing} can't be done right now. "
                         f"Trying again in {secs} ({tries}), but waiting won't fix this today - the limit resets tomorrow.")
    if e["reason"] == "rate_limit":
        return "warning", (f"**{who} asked us to slow down** - the free plan only allows so much AI use per minute. "
                           f"Waiting {secs}, then carrying on ({tries}).")
    if e["reason"] == "busy":
        return "warning", f"**{who} is busy right now.** Trying again in {secs} ({tries})."
    if e["reason"] == "network":
        return "warning", f"**Couldn't reach {who}** - is the internet connection working? Trying again in {secs} ({tries})."
    return "error", f"**{who} still refused after {e['attempts']} tries**, so part of the check was skipped while {doing}."


def reopen(b: Bible, flag_id: int):
    b.db.execute("UPDATE flags SET status='open' WHERE flag_id=?", (flag_id,))
    b.db.commit()


# ------------------------------------------------------------------------------------------ sidebar
missing = missing_keys()
with st.sidebar:
    html_block("""<div class="brand"><div class="logo">CC</div>
                  <div><div class="brand-name">Continuity Checker</div><div class="brand-tag">Story checks for episodic scripts</div></div></div>""")
    html_block("<div class='side-h'>Current show</div>")
    if ss.get("sources"):
        html_block(f"""<div class="side-card"><div class="t">{esc(nice(ss.get('show_name', '')))}</div>
                       <div class="s">{esc(nice(ss.get('genre', 'generic')))} · {len(ss['sources'])} file(s)</div></div>""")
    else:
        html_block("<div class='side-card'><div class='s'>No show loaded yet</div></div>")
    html_block("<div class='side-h'>Type of check</div>")
    options = ["Smart check (uses AI)", "Quick check (no AI)"]
    captions = ["Reads the story like an editor. Catches far more.", "Spots obvious patterns. Instant."]
    if missing:
        ss["check_type"] = options[1]
        st.radio("Type of check", options, captions=captions, index=1, disabled=True, label_visibility="collapsed")
        st.info("Smart check isn't set up yet. Ask your admin to add the AI keys.", icon=":material/info:")
    else:
        ss["check_type"] = st.radio("Type of check", options, captions=captions, label_visibility="collapsed",
                                    index=0 if ss.get("check_type", options[0]).startswith("Smart") else 1)
    st.divider()
    st.caption(":material/lock: For private scripts, run this app on your own computer rather than a shared website.")

# ------------------------------------------------------------------------------------------ step bar
ss.setdefault("step", STEPS[0])
if ss.get("step") not in STEPS + [None]:  # session from an older version of the app
    ss["step"] = STEPS[0]
with st.container(key="stepbar"):
    st.segmented_control("Step", STEPS, key="step", on_change=keep_step, format_func=STEP_LABEL.get,
                         label_visibility="collapsed", width="stretch")
page = ss.get("step") or ss.get("_last_step", STEPS[0])
ss["_last_step"] = page

# ------------------------------------------------------------------------------------------ 1. Load scripts
if page == "load":
    html_block(f"""
    <div class="hero">
      <div>
        <div class="eyebrow">Script continuity · for writers and editors</div>
        <div class="hero-title">Catch story mistakes<br>before your audience does.</div>
        <p class="hero-sub">Upload your episode scripts. We find the places where the story contradicts itself -
           a sister who becomes a cousin, a character who talks after dying, a letter that comes back after it was burned.</p>
        <div class="hero-points"><span>✓ Shows both clashing lines</span><span>✓ Knows twists from mistakes</span>
           <span>✓ Ranked by how serious</span></div>
      </div>
      <div class="hero-card">
        <div class="hc-top">{badge('Serious', 'b-high', True)}{badge('Likely mistake', 'b-verdict')}</div>
        <div class="hc-title">Riya is Meera's sister - then her cousin</div>
        <div class="hc-line"><small>EPISODE 2</small>“Riya is my younger sister.”</div>
        <div class="hc-line"><small>EPISODE 6</small>“Meet my cousin Riya.”</div>
      </div>
    </div>""")
    if ON_CLOUD:   # a hosted copy must not read folders on the server
        page_head(1, "Load your scripts", "Try a demo show, or upload a zip with one text file per episode.")
        t_demo, t_zip = st.tabs([":material/movie: Demo shows", ":material/folder_zip: Upload a zip"])
        t_folder = None
    else:
        page_head(1, "Load your scripts", "Try a demo show, upload a zip with one text file per episode, or point to a folder.")
        t_demo, t_zip, t_folder = st.tabs([":material/movie: Demo shows", ":material/folder_zip: Upload a zip",
                                           ":material/folder_open: Folder on this computer"])
    with t_demo:
        cols = st.columns(len(DEMOS), gap="medium")
        for col, (key, (title, genre, initials, blurb)) in zip(cols, DEMOS.items()):
            with col, st.container(key=f"card_demo_{key}"):
                html_block(f"""<div class="tile">{initials}</div><div class="card-title">{title}</div>
                               <div class="badges">{badge(nice(genre), 'b-kind')}</div><p class="card-text">{blurb}</p>""")
                if st.button("Load this show", key=f"demo_{key}", width="stretch", icon=":material/download_for_offline:"):
                    ss["sources"] = load_sources(ROOT / "eval" / "synthetic" / key)
                    ss["show_name"], ss["genre"] = key, genre
                    ss.pop("aliases", None); ss.pop("summary", None)
                    st.toast(f"Loaded {title}", icon=":material/check_circle:")
    with t_zip:
        with st.container(key="card_zip"):
            up = st.file_uploader("A zip file with one text file per episode", type=["zip"])
            if up is not None and st.button("Load this zip", type="primary", icon=":material/upload:"):
                ss["sources"] = load_uploaded_zip(up.getvalue()); ss["show_name"] = Path(up.name).stem
                ss.pop("aliases", None); ss.pop("summary", None)
                st.toast(f"Loaded {up.name}", icon=":material/check_circle:")
    if t_folder is not None:
      with t_folder:
        with st.container(key="card_folder"):
            pth = st.text_input("Folder that holds the episode files", placeholder="/Users/me/Scripts/My Show")
            if pth and st.button("Load this folder", type="primary", icon=":material/folder_open:"):
                try:
                    ss["sources"] = load_sources(pth); ss["show_name"] = Path(pth).name
                    ss.pop("aliases", None); ss.pop("summary", None)
                    st.toast(f"Loaded {Path(pth).name}", icon=":material/check_circle:")
                except Exception as e:
                    st.error(f"Couldn't open that folder: {e}")

    if ss.get("sources"):
        html_block(f"<div class='sec'>{esc(nice(ss.get('show_name', '')))}</div>")
        with st.container(key="card_show"):
            g = ss.get("genre", "generic")
            ss["genre"] = st.pills("What kind of story is it?", GENRES, format_func=nice,
                                   default=g if g in GENRES else "generic") or g
            st.caption("This tells the checker which twists are normal for the story - like disguises in a romance "
                       "or coming back to life in a fantasy. " + load_profile(ss["genre"]).get("description", ""))
        min_cov = float(load_config()["pipeline"].get("min_parse_coverage", 0.95))
        try:
            with st.spinner("Reading the scripts..."):
                pr = parse_sources(ss["sources"], llm=LLM(current_cfg(), cache_path=CACHE), min_coverage=min_cov)
        except Exception as e:
            st.error(f"We couldn't read these scripts: {e}"); st.stop()
        ss["parse"] = pr
        c1, c2, c3 = st.columns(3)
        c1.metric("Episodes", len(pr.episodes))
        c2.metric("Lines", f"{len(pr.all_lines()):,}")
        c3.metric("Script understood", f"{pr.coverage:.0%}")
        if pr.coverage < min_cov:
            st.error("Some parts of the script couldn't be read. Have a look at the examples below before you continue.")
        for w in pr.warnings:
            st.warning(w)
        if pr.unparsed_samples:
            with st.expander("Text we couldn't read", icon=":material/warning:"):
                st.code("\n".join(pr.unparsed_samples[:20]))
        with st.expander("Peek at episode 1", icon=":material/visibility:"):
            st.dataframe([{"Time": l.ts_start, "Who": l.speaker or l.type.capitalize(), "Line": l.text}
                          for l in pr.episodes[0].lines[:60]], width="stretch", hide_index=True)
        next_button("names", "Next: check character names")

# ------------------------------------------------------------------------------------------ 2. Characters
elif page == "names":
    page_head(2, "Check character names", "Scripts often call the same person different things (“LADY ANNA” and “ANNA”). "
              "We join names that clearly belong together, and ask you about the unclear ones.")
    if not ss.get("parse"):
        empty("upload_file", "No show loaded yet", "Load a show first, then come back here.", "load", "Go to Load scripts")
    out = out_dir(); out.mkdir(parents=True, exist_ok=True)
    dec_path = out / "alias_decisions.csv"
    if "aliases" not in ss:
        with st.spinner("Comparing character names..."):
            ss["aliases"] = resolve_aliases(ss["parse"].episodes, llm=LLM(current_cfg(), cache_path=CACHE),
                                            decisions_path=dec_path, log=lambda *_: None)
    res = ss["aliases"]
    pend = [p for p in res.pairs if p.status == "pending"]

    c1, c2 = st.columns(2)
    c1.metric("Characters", len(res.characters))
    c2.metric("Names to confirm", len(pend))

    with st.expander(f"All characters ({len(res.characters)})", expanded=not pend, icon=":material/groups:"):
        q = st.text_input("Search characters", placeholder="Type a name...", label_visibility="collapsed",
                          icon=":material/search:")
        rows = [{"Character": c, "Also called": ", ".join(x for x in l if x != c) or "-"}
                for c, l in sorted(res.characters.items()) if not q or q.lower() in (c + " ".join(l)).lower()]
        st.dataframe(rows, width="stretch", hide_index=True)

    if not pend:
        html_block("""<div class="empty"><div class="tile soft"><span class="mi">check_circle</span></div><div class="empty-title">All names are clear</div>
                      <div class="empty-text">There's nothing for you to decide here.</div></div>""")
    else:
        html_block("<div class='sec'>Are these the same person?</div>")
        lines = {l.line_id: l for l in ss["parse"].all_lines()}
        decisions = []
        for i, p in enumerate(pend):
            with st.container(key=f"card_pair_{i}"):
                html_block(f"""<div class="pair">{esc(p.a)}<span class="vs">⇄</span>{esc(p.b)}</div>
                               <p class="card-text">{esc(p.a)} appears in episodes {', '.join(map(str, p.eps_a[:12]))} ·
                               {esc(p.b)} appears in episodes {', '.join(map(str, p.eps_b[:12]))}</p>""")
                hits = [lid for lid in (p.address_hits + p.action_hits)[:6] if lid in lines]
                if hits:
                    with st.expander("Show example lines", icon=":material/subject:"):
                        for lid in hits:
                            html_block(f"<p class='ctx'>{esc(lines[lid].render()[:220])}</p>")
                choice = st.segmented_control("Same person?", ["Not sure", "Same person", "Different people",
                                                               "Same, from a certain episode"],
                                              default="Not sure", key=f"al{i}", label_visibility="collapsed")
                ep_from = None
                if choice == "Same, from a certain episode":
                    ep_from = st.number_input("Starting from episode", min_value=1, value=1, key=f"alf{i}")
                if choice and choice != "Not sure":
                    decisions.append([p.a, p.b, "different" if choice == "Different people" else "same", ep_from or "", ""])
        st.progress(len(decisions) / len(pend), text=f"You've answered {len(decisions)} of {len(pend)} · "
                    "anything left on “Not sure” is kept as two people")
        if st.button("Save my answers", type="primary", disabled=not decisions, icon=":material/save:"):
            old = list(csv.reader(open(dec_path, encoding="utf-8")))[1:] if dec_path.exists() else []
            keep = {(r[0], r[1]): r for r in old}
            for d in decisions:
                keep[(d[0], d[1])] = d
            with open(dec_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(["label_a", "label_b", "decision", "ep_from", "ep_to"]); w.writerows(keep.values())
            ss.pop("aliases", None)
            st.toast(f"Saved {len(decisions)} answer(s)", icon=":material/save:")
            st.rerun()
    next_button("check", "Next: run the check")

# ------------------------------------------------------------------------------------------ 3. Run check
elif page == "check":
    page_head(3, "Run the check", "We read every episode, note the facts about each character, and compare them across the show.")
    if not ss.get("sources"):
        empty("upload_file", "No show loaded yet", "Load a show first, then come back here.", "load", "Go to Load scripts")
    out = out_dir()
    with st.container(key="card_run"):
        c1, c2, c3 = st.columns(3)
        for col, label, value in ((c1, "Show", nice(ss.get("show_name", ""))), (c2, "Kind of story", nice(ss.get("genre", "generic"))),
                                  (c3, "Type of check", ss["check_type"])):
            col.markdown(f"<div class='side-label'>{label}</div><div class='side-where'>{esc(value)}</div>",
                         unsafe_allow_html=True)
        inc = False
        if (out / "bible.sqlite").exists():
            inc = st.toggle("Only check new episodes (faster - keeps the results from last time)", value=False)
        lo, hi = estimate_minutes() if use_ai() else (0, 0)
        if use_ai() and lo:
            n_eps = len(ss["parse"].episodes) if ss.get("parse") else "?"
            st.info(f"**Expected time: about {lo}-{hi} minutes** for {n_eps} episodes. The free AI plans only allow "
                    "so much use per minute, so long shows take a while. Episodes you've checked before are reused "
                    "and go much faster. Keep this page open while it runs.", icon=":material/schedule:")
        elif not use_ai():
            st.caption(":material/bolt: The quick check takes a few seconds.")
        for who in ss.get("daily_limits", []):
            st.warning(f"**{who}'s free daily limit was used up during an earlier check.** If you start now, the check "
                       "will keep waiting for it and parts may be skipped. It resets tomorrow - or use the quick check "
                       "for now.", icon=":material/hourglass_disabled:")
        start = st.button("Start the check", type="primary", width="stretch", icon=":material/play_arrow:")
    if start:
        t0 = time.time()
        events: list[dict] = []
        state = {"frac": 0.0, "text": "Starting"}
        with st.status("Checking your scripts...", expanded=True) as box:
            bar, msg, timer, notice = st.progress(0.0), st.empty(), st.empty(), st.empty()

            def show_timer():
                spent, frac = time.time() - t0, state["frac"]
                if frac >= 0.12 and spent > 5:
                    left = f"about {fmt_time(spent * (1 - frac) / frac)} left"
                elif hi:
                    left = f"expected {lo}-{hi} min in total"
                else:
                    left = "almost done"
                timer.caption(f":material/timer: {fmt_time(spent)} so far · {left}")

            def prog(text, frac):
                state.update(frac=frac, text=text)
                bar.progress(min(1.0, frac)); msg.markdown(f"_{text}..._"); show_timer()

            def waiting(e):
                events.append(e)
                kind, text = wait_message(e)
                if e["reason"] == "daily_limit":
                    who = PROVIDER.get(e["provider"], e["provider"].capitalize())
                    ss.setdefault("daily_limits", [])
                    if who not in ss["daily_limits"]:
                        ss["daily_limits"].append(who)
                (notice.error if kind == "error" else notice.warning)(text, icon=":material/hourglass_top:")
                show_timer()
            try:
                s = run(ss.get("show_name"), out, cfg=current_cfg(), genre=ss.get("genre"), incremental=inc,
                        sources=ss["sources"], progress=prog, log=lambda *_: None, on_wait=waiting)
                ss["summary"], ss["wait_events"] = s, events
                notice.empty()
                gave_up = any(e["reason"] == "gave_up" for e in events)
                box.update(label=f"Check finished in {fmt_time(time.time() - t0)}"
                                 + (" - but some parts were skipped (see below)" if gave_up else ""),
                           state="complete", expanded=False)
                st.toast("Check finished, but some parts were skipped" if gave_up else "Check finished!",
                         icon=":material/warning:" if gave_up else ":material/celebration:")
            except Exception as e:
                box.update(label="The check stopped", state="error")
                st.error(f"Something went wrong: {e}")
    s = ss.get("summary")
    if s:
        b = Bible(out / "bible.sqlite")
        found = len([f for f in ranked_flags(b) if f["status"] == "open"])
        # Skipped episodes: one plain message per cause instead of one technical error per episode
        skipped_eps: dict[str, list[str]] = {}
        other_warnings = []
        for w in s["warnings"]:
            m = re.match(r"episode (\d+): extraction failed \((.*)\); continuing$", w, re.S)
            if not m:
                other_warnings.append(w); continue
            who_m = re.search(r"\] (\w+):", m.group(2))
            who = PROVIDER.get(who_m.group(1), who_m.group(1).capitalize()) if who_m else "The AI"
            err = m.group(2)
            if "HTTP 429" in err:
                cause = (f"{who}'s free daily limit is used up" if wait_reason(429, err) == "daily_limit"
                         else f"{who} kept asking us to slow down")
            elif "HTTP" in err:
                cause = f"{who} had a problem on its side"
            else:
                cause = f"{who} couldn't be reached or gave an answer we couldn't use"
            skipped_eps.setdefault(cause, []).append(m.group(1))
        for cause, eps in skipped_eps.items():
            listed = ", ".join(eps[:-1]) + f" and {eps[-1]}" if len(eps) > 1 else eps[0]
            st.error(f"**Episode{'s' if len(eps) > 1 else ''} {listed} {'were' if len(eps) > 1 else 'was'} skipped** "
                     f"because {cause}. Problems in {'those episodes' if len(eps) > 1 else 'that episode'} won't show up "
                     "yet. Run the check again later with **“Only check new episodes”** turned on - only the skipped "
                     "episodes will be redone.", icon=":material/error:")
        for w in other_warnings:
            st.warning(w)
        events = ss.get("wait_events") or []
        if events:
            waited = sum(e["seconds"] for e in events)
            with st.expander(f"Why this check was slower: the AI made us wait {fmt_time(waited)} in total",
                             icon=":material/hourglass_top:"):
                seen: dict[str, int] = {}
                for e in events:
                    key = wait_message({**e, "seconds": 0, "attempt": 0})[1].split(" Trying")[0].split(" Waiting")[0]
                    seen[key] = seen.get(key, 0) + 1
                for text, n in seen.items():
                    st.markdown(f"- {text}" + (f" _(×{n})_" if n > 1 else ""))
        if skipped_eps:
            with st.expander("Technical details (for your admin)", icon=":material/code:"):
                st.code("\n\n".join(w for w in s["warnings"] if w not in other_warnings))
        c = st.columns(4)
        c[0].metric("Problems to review", found)
        c[1].metric("Episodes", s["episodes"])
        c[2].metric("Characters", s["characters"])
        c[3].metric("Time taken", fmt_time(s["seconds"]))
        if found:
            next_button("review", f"Review {found} problem(s)")
        else:
            html_block("""<div class="empty"><div class="tile soft"><span class="mi">check_circle</span></div><div class="empty-title">No problems found</div>
                          <div class="empty-text">The story holds together across every episode.</div></div>""")

# ------------------------------------------------------------------------------------------ 4. Review
elif page == "review":
    page_head(4, "Review the problems", "Most serious first. Each one shows the two lines that clash, so you can decide in seconds.")
    bp = out_dir() / "bible.sqlite"
    if not bp.exists():
        empty("fact_check", "Nothing to review yet", "Run the check first and the problems will appear here.", "check", "Go to Run check")
    b = Bible(bp)
    everything = ranked_flags(b, include_all=True)
    main = [f for f in everything if f["verdict"] in ("contradiction", "unsure")]
    counts = {k: sum(f["status"] == k for f in main) for k in STATUS}
    done = len(main) - counts["open"]

    c = st.columns(4)
    for col, (k, label) in zip(c, STATUS.items()):
        col.metric(label, counts[k])
    if main:
        st.progress(done / len(main), text=f"You've reviewed {done} of {len(main)}")

    with st.container(key="card_filters"):
        f1, f2 = st.columns([3, 2])
        status_pick = f1.segmented_control("Show", list(STATUS.values()) + ["All"], default="To review")
        sev_pick = f2.pills("How serious", list(SEVERITY.values()), selection_mode="multi", default=list(SEVERITY.values()))
        f3, f4 = st.columns([3, 2], vertical_alignment="center")
        search = f3.text_input("Search", placeholder="Search by character or words in the script...",
                               label_visibility="collapsed", icon=":material/search:")
        show_fine = f4.toggle("Also show things the checker thinks are fine")

    pool = everything if show_fine else main
    sev_keys = [k for k, v in SEVERITY.items() if v in (sev_pick or [])]
    status_key = next((k for k, v in STATUS.items() if v == status_pick), None)
    flags = [f for f in pool if f["severity"] in sev_keys and (status_key is None or f["status"] == status_key)]
    if search:
        s_low = search.lower()
        flags = [f for f in flags if s_low in json.dumps([f.get("note"), f.get("reason"), f.get("a"), f.get("b"),
                                                          f.get("line_b_row")], default=str).lower()]
    if main and done == len(main) and status_key == "open":
        html_block("""<div class="empty"><div class="tile soft"><span class="mi">check_circle</span></div><div class="empty-title">All done</div>
                      <div class="empty-text">Every problem has been reviewed. Download your report in the next step.</div></div>""")
    elif not flags:
        html_block("""<div class="empty"><div class="tile soft"><span class="mi">search_off</span></div><div class="empty-title">Nothing matches</div>
                      <div class="empty-text">Try changing the filters above.</div></div>""")
    else:
        st.caption(f"Showing {min(len(flags), ss.setdefault('review_limit', 10))} of {len(flags)} problem(s)")

    limit = ss.setdefault("review_limit", 10)
    for f in flags[:limit]:
        fid = f["flag_id"]
        a, bb, lb = f.get("a") or {}, f.get("b") or {}, f.get("line_b_row") or {}
        kind = KIND.get(f["rule"], nice(f["rule"]))
        with st.container(key=f"card_sev_{f['severity']}_{fid}"):
            badges = (badge(SEVERITY.get(f["severity"], "-"), f"b-{f['severity']}", True) + badge(kind, "b-kind")
                      + badge(VERDICT.get(f["verdict"], nice(f["verdict"])), "b-verdict"))
            if f["status"] != "open":
                badges += badge("✓ " + STATUS.get(f["status"], f["status"]), "b-done")
            html_block(f"<div class='flag-title'>{esc(f['note'] or kind)}</div><div class='badges'>{badges}</div>")

            l, r = st.columns(2, gap="medium")
            if a:
                with l:
                    side(b, "First", a)
            if bb or lb:
                x = bb or {"episode": lb["episode"], "line_id": lb["line_id"], "quote": lb["text"], "speaker": lb["speaker"]}
                with r:
                    side(b, "Later", x)
            if f["rule"] == "notes_mismatch":
                p = json.loads(f["payload"] or "{}")
                html_block(f"""<div class="side-label">The episode {p.get('episode')} notes say</div>
                               <div class="quote">{esc(p.get('claim', ''))}</div>""")
            html_block(f"<div class='why'><b>Why this was flagged:</b> {esc(f['reason'])}</div>")
            if not f["reason_verified"]:
                st.caption(":material/warning: The AI's explanation quoted words that aren't in the script, so double-check this one.")

            if f["status"] == "open":
                k1, k2, k3, k4 = st.columns([2, 2, 2, 1])
                note = ss.get(f"n{fid}", "")
                for col, dec, label, icon in ((k1, "accepted", "Real mistake", ":material/check_circle:"),
                                              (k2, "dismissed", "Not a problem", ":material/block:"),
                                              (k3, "intentional", "Done on purpose", ":material/theater_comedy:")):
                    if col.button(label, key=f"{dec}{fid}", width="stretch", icon=icon,
                                  type="primary" if dec == "accepted" else "secondary"):
                        b.decide(fid, dec, note, feedback_log=out_dir() / "feedback_log.jsonl")
                        st.toast(f"Marked as “{STATUS[dec]}”", icon=":material/thumb_up:")
                        st.rerun()
                with k4.popover("Note", icon=":material/edit_note:", width="stretch"):
                    st.text_input("Note for this problem (optional)", key=f"n{fid}")
            else:
                if st.button("Undo", key=f"undo{fid}", icon=":material/undo:"):
                    reopen(b, fid)
                    st.toast("Moved back to “To review”", icon=":material/undo:")
                    st.rerun()
    if len(flags) > limit:
        if st.button(f"Show more ({len(flags) - limit} left)", width="stretch", icon=":material/expand_more:"):
            ss["review_limit"] = limit + 10
            st.rerun()
    if main and done == len(main):
        next_button("download", "Next: download the report")

# ------------------------------------------------------------------------------------------ 5. Download
else:
    page_head(5, "Download your results", "Share the report with your team, or open the problem list in a spreadsheet.")
    out = out_dir()
    if not (out / "report.md").exists():
        empty("description", "No report yet", "Run the check first and your report will be ready here.", "check", "Go to Run check")
    main_files = [("report.md", "text/markdown", "description", "Full report", "Every problem, with the lines from the script."),
                  ("flags.csv", "text/csv", "table_view", "Problems list", "A spreadsheet you can sort, filter and share."),
                  ("characters.csv", "text/csv", "groups", "Characters", "Every character and the names they go by.")]
    cols = st.columns(3, gap="medium")
    for col, (fn, mime, icon, title, blurb) in zip(cols, main_files):
        p = out / fn
        with col, st.container(key=f"card_dl_{fn.replace('.', '_')}"):
            html_block(f"""<div class="tile"><span class="mi">{icon}</span></div><div class="card-title">{title}</div><p class="card-text">{blurb}</p>""")
            st.download_button("Download", p.read_bytes() if p.exists() else b"", file_name=fn, mime=mime, type="primary",
                               disabled=not p.exists(), width="stretch", key=f"dl_{fn}", icon=":material/download:")
    with st.expander("More files (for the technical team)", icon=":material/folder:"):
        for fn, mime in (("facts.csv", "text/csv"), ("alias_pairs.csv", "text/csv"),
                         ("feedback_log.jsonl", "application/json")):
            p = out / fn
            if p.exists():
                st.download_button(f"Download {fn}", p.read_bytes(), file_name=fn, mime=mime, key=f"dl_{fn}")
    with st.expander("Read the report here", expanded=True, icon=":material/article:"):
        st.markdown((out / "report.md").read_text(encoding="utf-8"))

html_block("<div class='footer'>Continuity Checker · every flag shows the exact lines, so you can check it in seconds</div>")
