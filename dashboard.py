"""dashboard.py — Streamlit dashboard, management-friendly "glimpse" view with a
neon dark theme.

Run:  streamlit run dashboard.py

Top of page (no clicks needed): a status strip (market regime, actionable count,
data health, last updated), a "what changed since last run" line, trade-plan
cards for the actual Buys, and a "high score but NOT a Buy — why" panel. The
book/cash tracking was removed 2026-08-18 at the user's request — they manage
position sizing themselves. Drill-down tabs below hold the full colour-coded
watchlist, the strategy Edge backtest (expectancy / profit
factor / max drawdown / out-of-sample, Tier 2 #8), per-stock charts, history,
news, and reports.
"""

import os
import json
import runtime_bootstrap
runtime_bootstrap.prepare()
import time
import requests

import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import config
import database as db
import data_fetcher
import backtester
import news_feed
import momentum
import target_timing

st.set_page_config(page_title="PSX Shariah Engine", layout="wide",
                   page_icon="📈")

# ====================== NEON THEME ========================================
NEON = {"cyan": "#00e5ff", "violet": "#a855f7", "green": "#00ffa3",
        "amber": "#ffd54a", "red": "#ff4d6d", "text": "#e7f0ff",
        "dim": "#9fb3d1"}

# Signal / risk accent colours (neon, high-contrast on the dark background).
NEON_SIG = {"Strong Buy": "#00ffa3", "Buy": "#3ae67f", "Watch": "#ffd54a",
            "Hold": "#9fb3d1", "Avoid": "#ff5d7a", "Exit": "#ff4d6d",
            "No data": "#8aa0c0"}
NEON_RISK = {"Low": "#00ffa3", "Medium": "#ffd54a", "High": "#ff4d6d"}
SIG_RANK = {"Strong Buy": 6, "Buy": 5, "Watch": 4, "Hold": 3, "Avoid": 2,
            "Exit": 1, "No data": 0}
PLOT_LINE = ["#00e5ff", "#a855f7", "#00ffa3", "#ffd54a", "#ff4d6d"]


def _inject_theme():
    st.markdown(
        """
        <style>
        .stApp {
          background:
            radial-gradient(1100px 560px at 10% -12%, rgba(0,229,255,0.13), transparent 60%),
            radial-gradient(1000px 520px at 102% -4%, rgba(168,85,247,0.15), transparent 55%),
            radial-gradient(900px 520px at 50% 118%, rgba(0,255,163,0.10), transparent 55%),
            linear-gradient(180deg,#070b16 0%, #0a1020 48%, #070b16 100%);
          background-attachment: fixed;
        }
        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stToolbar"] { right: 1rem; }
        h1, h2, h3 { color: #eaf6ff !important; letter-spacing:.3px; }
        h1 { text-shadow: 0 0 22px rgba(0,229,255,0.35); }
        h2 { text-shadow: 0 0 16px rgba(0,229,255,0.20); }
        hr { border-color: rgba(0,229,255,0.15) !important; }
        /* glassmorphic bordered containers (tiles, cards) */
        [data-testid="stVerticalBlockBorderWrapper"] {
          background: rgba(16,24,44,0.55);
          border: 1px solid rgba(0,229,255,0.18) !important;
          border-radius: 14px !important;
          box-shadow: 0 8px 30px rgba(0,0,0,0.45), inset 0 0 0 1px rgba(0,229,255,0.03);
          backdrop-filter: blur(7px);
          transition: border-color .2s ease, box-shadow .2s ease;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:hover {
          border-color: rgba(0,229,255,0.40) !important;
          box-shadow: 0 10px 36px rgba(0,0,0,0.5), 0 0 22px -6px rgba(0,229,255,0.5);
        }
        [data-testid="stMetricValue"] {
          color: #00e5ff; text-shadow: 0 0 14px rgba(0,229,255,0.45);
          font-weight: 800;
        }
        [data-testid="stMetricLabel"] { color: #9fb3d1; }
        [data-testid="stMetricDelta"] { color: #00ffa3; }
        /* sidebar */
        [data-testid="stSidebar"] {
          background: linear-gradient(180deg, rgba(11,17,34,0.92), rgba(7,11,22,0.96));
          border-right: 1px solid rgba(0,229,255,0.14);
        }
        /* tabs */
        [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid rgba(0,229,255,0.12); }
        [data-baseweb="tab"] {
          background: rgba(255,255,255,0.03); border-radius: 10px 10px 0 0;
          padding: 7px 15px; color: #cfe0ff;
        }
        [aria-selected="true"][data-baseweb="tab"] {
          background: rgba(0,229,255,0.13);
          box-shadow: inset 0 -2px 0 #00e5ff, 0 0 18px -6px rgba(0,229,255,0.7);
          color: #eaf6ff;
        }
        /* buttons */
        .stButton > button {
          background: linear-gradient(90deg, #00e5ff, #a855f7);
          color: #06101f; font-weight: 700; border: none; border-radius: 10px;
          box-shadow: 0 0 18px -4px rgba(0,229,255,0.6);
        }
        .stButton > button:hover { filter: brightness(1.12); color:#06101f; }
        /* inputs */
        [data-testid="stNumberInput"] input, [data-baseweb="select"] > div {
          background: rgba(10,16,32,0.7) !important;
          border-color: rgba(0,229,255,0.25) !important;
        }
        /* Quiet management layout: keep all controls and existing sections. */
        .stApp { background: #0b1220; color: #e6edf7; }
        h1, h2, h3 { text-shadow: none !important; letter-spacing: -.02em; }
        .block-container { max-width: 1480px; padding-top: 2rem; }
        [data-testid="stMetric"] { background: #142035; padding: 18px 22px;
          border: 1px solid #29384e; border-radius: 12px; }
        [data-testid="stMetricValue"] { text-shadow: none; color: #e6edf7; }
        [data-testid="stVerticalBlockBorderWrapper"] { box-shadow: none; background: #111d30; }
        .stButton > button { background: #2363a4; color: white; box-shadow: none; }
        [data-testid="stCaptionContainer"] { color: #b8c7da; }
        p, li { line-height: 1.6; }
        [data-baseweb="tab-list"] { flex-wrap: wrap; gap: 6px; }
        @media (max-width: 768px) { .block-container { padding: 1rem; }
          [data-testid="stMetric"] { padding: 12px; } }
        </style>
        """,
        unsafe_allow_html=True)


# ----------------------------- pills / helpers ----------------------------
def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _pill(text, hexc):
    r, g, b = _hex_rgb(hexc)
    return (f'<span style="background:rgba({r},{g},{b},0.13);color:{hexc};'
            f'border:1px solid rgba({r},{g},{b},0.55);border-radius:9px;'
            f'padding:2px 10px;font-size:13px;font-weight:700;white-space:nowrap;'
            f'text-shadow:0 0 7px rgba({r},{g},{b},0.45);'
            f'box-shadow:0 0 12px -3px rgba({r},{g},{b},0.7)">{text}</span>')


def sig_pill(sig):
    return _pill(sig or "—", NEON_SIG.get(sig, "#8aa0c0"))


def risk_pill(level):
    return _pill(f"{level} risk", NEON_RISK.get(level, "#8aa0c0"))


def news_pill(verdict):
    """Compact news verdict chip. verdict is the dict from news_feed.get(sym)."""
    if not verdict:
        return _pill("📰 no fresh news", "#8aa0c0")
    score = verdict.get("score", 50)
    delta = score - 50  # symmetric around neutral
    direction = verdict.get("direction", "neutral")
    mat = verdict.get("materiality", "normal")
    if direction == "positive":
        clr = NEON["green"]
        arrow = "▲"
    elif direction == "negative":
        clr = NEON["red"]
        arrow = "▼"
    else:
        clr = "#8aa0c0"
        arrow = "●"
    star = " ★" if mat in ("material_positive", "material_negative") else ""
    return _pill(f"📰 {arrow} {delta:+d}{star}", clr)


def _news_window(symbol, nv=None):
    """UNSCORED per-symbol news window for manual cross-verification. Shows the
    auto-fetched last-24h headlines (news_raw_24h.json, refreshed by news.yml on
    a cron — no manual routine). News carries ZERO score weight; this is purely
    so the user can eyeball real, source-linked headlines. Falls back to the
    LLM-judged summary only if it happens to exist."""
    items = news_feed.raw_headlines(symbol, limit=5)
    st.markdown("**📰 News — last 24h (not scored; for your manual check)**")
    if items:
        for it in items:
            pub = it.get("publisher") or "source"
            url, title = it.get("url"), it["title"]
            st.markdown(f"- [{title}]({url}) · _{pub}_" if url
                        else f"- {title} · _{pub}_")
    elif nv and nv.get("summary"):
        st.markdown(f"_{nv['summary']}_")
        for h, u in zip(nv.get("headlines", []), nv.get("sources", [])):
            st.markdown(f"- [{h}]({u})")
    else:
        st.caption("No allowlisted headlines fetched for this symbol in the last "
                   "24h. News never moves the score — this window is informational.")


_GLM_STYLE = {
    "highly_positive": (NEON["green"], "▲▲ highly +ve"),
    "positive":        (NEON["green"], "▲ +ve"),
    "neutral":         ("#8aa0c0",     "● neutral"),
    "negative":        (NEON["red"],   "▼ -ve"),
    "highly_negative": (NEON["red"],   "▼▼ highly -ve"),
}


# Causality is the tag that decides whether news is tradeable at all: only a
# "causal" item has a traceable mechanism to that company's cash flows.
_CAUSAL_STYLE = {"causal": (NEON["cyan"], "⛓ causal"),
                 "correlated": ("#8aa0c0", "≈ correlated"),
                 "noise": ("#8aa0c0", "· noise")}
_HORIZON_LABEL = {"single_session": "1-session", "multi_session": "multi-day",
                  "long_term": "long-term"}


def glm_pill(rating_dict):
    """Compact AI news-rating chip. rating_dict is news_feed.glm_rating(sym)."""
    if not rating_dict:
        return _pill("🤖 AI: —", "#8aa0c0")
    clr, label = _GLM_STYLE.get(rating_dict.get("rating"), ("#8aa0c0", "AI: ?"))
    return _pill(f"🤖 {label}", clr)


def analysis_pills(rating_dict):
    """Causality / horizon / confidence chips. Empty string for the old
    rating shape, so pre-Phase-3 files keep rendering unchanged."""
    if not rating_dict:
        return ""
    bits = []
    cz = rating_dict.get("causality")
    if cz in _CAUSAL_STYLE:
        c, lbl = _CAUSAL_STYLE[cz]
        bits.append(_pill(lbl, c))
    hz = rating_dict.get("horizon")
    if hz in _HORIZON_LABEL:
        bits.append(_pill(_HORIZON_LABEL[hz], "#8aa0c0"))
    conf = rating_dict.get("confidence")
    if isinstance(conf, (int, float)):
        bits.append(_pill(f"conf {conf:.0%}", "#8aa0c0"))
    return " ".join(bits)


_RATING_TEXT = {"highly_positive": "▲▲ highly +ve", "positive": "▲ +ve",
                "neutral": "● neutral", "negative": "▼ -ve",
                "highly_negative": "▼▼ highly -ve"}
_CAUSAL_TEXT = {"causal": "⛓", "correlated": "≈", "noise": "·"}


def news_cell(symbol):
    """Plain-text news read for a dataframe column: rating + causality mark.

    Tables cannot carry HTML, so the pills are compressed to text. "—" means no
    rating for this symbol, which is NOT the same as neutral news — it means the
    analyser had nothing in window to judge.
    """
    rv = news_feed.glm_rating(symbol)
    tag = ""
    if not rv:
        # Fall back to the SECTOR call, which moves this symbol's score just as
        # a company call does. Without it a card shows "—" beside a score that
        # visibly moved. Marked (sec) so it is never read as company news.
        rv, tag = news_feed.sector_rating(symbol), " (sec)"
    if not rv:
        return "—"
    txt = _RATING_TEXT.get(rv.get("rating"), "?")
    mark = _CAUSAL_TEXT.get(rv.get("causality"))
    return f"{txt} {mark}{tag}" if mark else f"{txt}{tag}"


def news_line(symbol, reason=True):
    """Pills + one-clause reason for a symbol, as an HTML fragment. Returns the
    'no rating' pill rather than nothing, so a card never looks as though news
    was checked and came back clean when it was simply never rated."""
    rv = news_feed.glm_rating(symbol)
    prefix = ""
    if not rv:
        rv = news_feed.sector_rating(symbol)
        if rv:
            prefix = _pill(f"🏷 sector: {config.SECTORS.get(symbol.upper())}",
                           "#8aa0c0") + " "
    out = prefix + glm_pill(rv) + (" " + analysis_pills(rv) if rv else "")
    if reason and rv and rv.get("reason"):
        out += (f' <span style="opacity:.75;font-size:12px">'
                f'{str(rv["reason"])[:140]}</span>')
    return out


def whatif_regime_note(actual_regime, assumed_regime, signal):
    """One-line label explaining what the signal WOULD be under an assumed
    regime. Approximation, not a re-run: risk-off soft-downgrades Buy→Watch
    per signal_generator; risk-on relaxes the chase guard and lets some
    regime-downgraded Watches surface as Buys. Actual regime → no note."""
    if not assumed_regime or assumed_regime == actual_regime:
        return ""
    if not config.REGIME_GATE_ENABLED:
        return 'Market direction is a warning only. The stock must still pass its own buying checks.'
    if assumed_regime == "risk-off" and signal in ("Buy", "Strong Buy"):
        return ("🌩 Under **risk-off**: signal would soft-downgrade to Watch "
                "(regime gate) — position size accordingly.")
    if assumed_regime == "risk-on" and signal in ("Buy", "Strong Buy"):
        return "☀ Under **risk-on**: signal holds; chase guard also loosens."
    if assumed_regime == "risk-on" and signal == "Watch":
        return ("☀ Under **risk-on**: if this Watch was regime-downgraded, "
                "it would revert to Buy (check main_reason).")
    return ""


def regime_pill(regime):
    if regime == "risk-on":
        return _pill("● Rising", NEON["green"])
    if regime == "risk-off":
        return _pill("● Falling", NEON["red"])
    return _pill("● Unknown", "#8aa0c0")


def fmt(x, d=2):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x:,.{d}f}"


def price_row(pairs):
    """Entry/Stop/Target strip as plain markdown. NOT st.metric: that widget
    lives in a lazily-imported JS chunk, and a browser holding a cached page
    shell from an earlier Cloud build 404s it ("Importing a module script
    failed") — hiding the three numbers that matter most on a trade card."""
    cells = "".join(
        f'<div style="flex:1;min-width:72px">'
        f'<div style="opacity:.55;font-size:11px;letter-spacing:.4px;'
        f'text-transform:uppercase">{label}</div>'
        f'<div style="font-size:19px;font-weight:700;color:{color}">{value}</div>'
        f'</div>'
        for label, value, color in pairs)
    return (f'<div style="display:flex;gap:10px;margin:8px 0 10px">{cells}</div>')


def neon_fig(fig, height=None):
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color="#cfe0ff"),
                      margin=dict(l=10, r=10, t=34, b=10),
                      legend=dict(bgcolor="rgba(0,0,0,0)",
                                  bordercolor="rgba(0,229,255,0.15)"))
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.06)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.06)", zeroline=False)
    if height:
        fig.update_layout(height=height)
    return fig


def why_not_buy(reason):
    """Pull the most relevant 'why it isn't a Buy' clause out of main_reason."""
    if not reason:
        return ""
    segs = [s.strip() for s in str(reason).split(";") if s.strip()]
    for kw in ("Downgraded", "breakdown", "negative news", "Shariah", "regime",
               "risk/reward", "manipulation", "confidence", "No usable price"):
        for s in segs:
            if kw.lower() in s.lower():
                return s
    return segs[-1] if segs else ""


def changes_since_last():
    """Symbols whose signal changed vs the previous run cycle."""
    ups, downs = [], []
    for s in config.STOCKS:
        h = db.run_history(s, 2)
        if len(h) >= 2 and h[0]["signal"] != h[1]["signal"]:
            cur, prev = h[0]["signal"], h[1]["signal"]
            (ups if SIG_RANK.get(cur, 0) > SIG_RANK.get(prev, 0) else downs).append(
                (s, prev, cur))
    return ups, downs


# ----------------------------- cached backtests ---------------------------
# fetch_eod hits the network with no cache, so backtests are expensive. Cache
# hard and only run the universe-wide one behind a button.
@st.cache_data(ttl=300, show_spinner=False)
def freshest_news():
    import news_desk
    return news_desk.load_freshest()


def runtime_engine_state():
    """Published engine state, using the same refresh path as research JSON."""
    import remote_data
    return remote_data.fetch_json(".engine-state.json", branch="runtime-state", ttl=60, timeout=4)


@st.cache_data(ttl=3600, show_spinner=False)
def bt_symbol(sym, data_version=None):
    return backtester.backtest(sym)


@st.cache_data(ttl=3600, show_spinner=False)
def bt_portfolio(data_version=None):
    return backtester.backtest_portfolio()


def _auto_refresh():
    """Refresh an open dashboard without discarding its current session."""
    secs = int(getattr(config, "DASHBOARD_REFRESH_SECONDS", 300))
    if secs <= 0:
        return
    st.session_state["_dashboard_refreshed_at"] = time.monotonic()

    @st.fragment(run_every=secs)
    def refresh_tick():
        if time.monotonic() - st.session_state["_dashboard_refreshed_at"] >= secs:
            st.rerun()

    refresh_tick()


def _inject_compact_css():
    st.markdown(
        """
        <style>
        [data-testid="stVerticalBlockBorderWrapper"] { padding: 2px !important; }
        [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stMarkdownContainer"] p {
          margin-bottom: 2px;
        }
        [data-testid="stMetricValue"] { font-size: 1rem !important; }
        [data-testid="stMetricLabel"] { font-size: 0.72rem !important; }
        h2 { font-size: 1.05rem !important; margin-top: 0.3rem !important; }
        h3, .stMarkdown h3 { font-size: 0.95rem !important; }
        [data-testid="stCaptionContainer"] { font-size: 0.72rem !important; }
        .block-container { padding-top: 1.2rem !important; padding-bottom: 1rem !important; }
        [data-testid="column"] { gap: 0.3rem !important; }
        </style>
        """,
        unsafe_allow_html=True)


# ----------------------------- load ---------------------------------------
_inject_theme()
# Public access confirmed by the owner. Remove obsolete password-link parameters.
if "k" in st.query_params:
    del st.query_params["k"]
_auto_refresh()
# Fast first paint: use a tiny latest-run snapshot before touching SQLite.
rows = []
_snapshot_fallback = False
# Runtime engine commits live on runtime-data so they do not redeploy this app.
# Pull only the tiny latest snapshot for first paint.
try:
    import remote_data
    _snapshot = remote_data.fetch_json("dashboard_snapshot.json", branch="runtime-state", ttl=60, timeout=4)
    if not isinstance(_snapshot, dict):
        raise ValueError("Runtime snapshot refresh unavailable")
    rows = [dict(row) for row in (_snapshot.get("rows") or [])]
except Exception:
    _snapshot_fallback = True
    try:
        with open("dashboard_snapshot.json", "r", encoding="utf-8") as _sf:
            rows = list((json.load(_sf).get("rows") or []))
    except Exception:
        rows = []

if not rows:
    # Deployment main intentionally carries no large runtime DB. The bundled
    # snapshot is the final startup fallback; never create an empty DB during
    # first paint because that masks a runtime-state fetch problem.
    st.title("PSX Shariah Engine")
    st.error("Runtime snapshot is temporarily unavailable. Please refresh shortly.")
    st.stop()

if _snapshot_fallback:
    st.warning("Live technical snapshot refresh is unavailable. Showing the local cached snapshot with its original timestamps.")

from history_view import explain_run
for _r in rows:
    _r['main_reason'], _r['main_risk'] = explain_run(_r)
if not rows:
    st.title("PSX Shariah Engine")
    st.warning("No runs stored yet. Run `python main.py run` first.")
    st.stop()

latest = pd.DataFrame(rows).sort_values("final_score", ascending=False,
                                        na_position="last")
for col in ("relative_strength", "market_regime", "buy_zone_low", "buy_zone_high"):
    if col not in latest.columns:
        latest[col] = None

latest_by_symbol = {r["symbol"]: r for r in rows}

regime = (latest["market_regime"].dropna().iloc[0]
          if latest["market_regime"].notna().any() else "unknown")
# run_time is written by the engine as a naive local timestamp, and the cloud
# runs set TZ=Asia/Karachi, so it is already Pakistan wall-clock (PKT, a fixed
# UTC+5 with no DST). Show it as-is and measure age against PKT now — do NOT
# add another +5h (that double-shifted the time and made age go negative).
_latest_pkt = pd.to_datetime(latest["run_time"].max())
_latest_pkt = _latest_pkt.tz_localize("Asia/Karachi") if _latest_pkt.tzinfo is None else _latest_pkt.tz_convert("Asia/Karachi")
last_updated = _latest_pkt.strftime("%m-%d %H:%M") + " PKT"
# Honest staleness flag: the cloud may pause runs (off-hours, weekends, paused
# Action) — in that case signals here describe yesterday's market, not today's.
# Compare against PKT now so the age matches the stored PKT run_time.
import session_calendar as calendar
_now_pkt = pd.Timestamp.now(tz="Asia/Karachi")
_market_live = calendar.is_live(_now_pkt.to_pydatetime())
_age_hours = (_now_pkt - _latest_pkt).total_seconds() / 3600
_amber = getattr(config, "DATA_FRESHNESS_AMBER_HOURS", 4)
_red = getattr(config, "DATA_FRESHNESS_RED_HOURS", 24)

# Two DIFFERENT questions were being answered with one number, and since the
# loop stopped committing the database on every cycle the answer was wrong:
#
#   "is the engine alive?"        -> .engine-state.json checked_at, written and
#                                    committed EVERY cycle even when nothing
#                                    moved. This is the liveness question.
#   "how old is the market data?" -> the completed session the numbers describe.
#
# The newest `runs` row answered neither well. It is only committed when the
# signal digest changes, so on a Monday morning it reported 59 hours old while
# the engine had in fact run ten minutes earlier. Liveness now drives the
# colour, because a stopped pipeline is the failure worth shouting about; the
# session is stated separately, because "Friday's numbers on a Monday morning"
# is the completed-session contract working, not a fault.
_engine_state, _checked_age_h = {}, None
try:
    _engine_state = runtime_engine_state()
    if _engine_state is None:
        with open(".engine-state.json", encoding="utf-8") as _esf:
            _engine_state = json.load(_esf)
    _ck = pd.to_datetime(_engine_state.get("checked_at"))
    if _ck is not None and not pd.isna(_ck):
        _ck = _ck.tz_localize("UTC") if _ck.tzinfo is None else _ck
        _checked_age_h = (pd.Timestamp.now(tz="UTC") - _ck).total_seconds() / 3600
except (OSError, ValueError, TypeError):
    _engine_state, _checked_age_h = {}, None

# Fall back to the run_time age when the state file is absent (older checkouts,
# local runs): a missing hint must not make a stale dashboard look fresh.
if _checked_age_h is not None:
    _age_hours = _checked_age_h
if _age_hours >= _red:
    _stale_level, _stale_color, _stale_label = "red", NEON["red"], "STALE"
elif _age_hours >= _amber:
    _stale_level, _stale_color, _stale_label = "amber", NEON["amber"], "aging"
else:
    _stale_level, _stale_color, _stale_label = "fresh", NEON["green"], "fresh"
if not _market_live:
    _stale_color, _stale_label = NEON["amber"], "market closed"
if _checked_age_h is not None:
    _ago = (f"{_checked_age_h * 60:.0f} min ago" if _checked_age_h < 1.5
            else f"{_checked_age_h:.1f}h ago")
    _sessions = sorted(set(latest.get("decision_session", pd.Series(dtype=str)).dropna().astype(str)))
    _sess = ", ".join(_sessions) or "unavailable"
    _last_updated_html = (
        f'<span style="color:{_stale_color}">engine checked {_ago}</span>'
        f' <span style="font-size:11px;opacity:.7">({_stale_label}) · '
        f'signals from session {_sess}</span>')
else:
    _last_updated_html = (f'<span style="color:{_stale_color}">{last_updated}</span>'
                          f' <span style="font-size:11px;opacity:.7">'
                          f'({_stale_label}, {_age_hours:.1f}h old)</span>')
good = int((latest["data_quality"] == "good").sum())

# ----------------------------- sidebar ------------------------------------
compact = True
st.session_state["compact"] = True
st.sidebar.caption(config.DISCLAIMER)
st.sidebar.caption(f"Per-trade risk {config.RISK['max_risk_per_trade_pct']}% · max {config.RISK['max_position_pct']}% per stock.")

# ----------------------------- header + status strip ----------------------
st.markdown("""<style>
.stApp{background:#08111e!important;background-image:none!important}
.block-container{max-width:1220px;padding-top:2rem;padding-bottom:2rem}
h1{font-size:27px!important;text-shadow:none!important;margin-bottom:0!important}
h2,h3{font-size:18px!important;text-shadow:none!important}
[data-testid="stCaptionContainer"]{color:#9db1ca}
[data-testid="stTabs"] [data-baseweb="tab-list"]{gap:8px;margin:12px 0 20px}
[data-testid="stTabs"] [data-baseweb="tab"]{background:#17263c;border-radius:6px;padding:8px 12px}
.desk-note{padding:12px 16px;background:#111e30;border:1px solid #2b4058;border-radius:10px;color:#a9bfd4;font-size:13px}
</style>""", unsafe_allow_html=True)
st.title("PSX trading desk")
st.caption("Intraday momentum and swing opportunities · prices, reasons and risk in one view")


def tile(col, label, value_html, sub=""):
    with col:
        box = st.container(border=True)
        box.markdown(
            f'<div style="font-size:12px;opacity:.65">{label}</div>'
            f'<div style="font-size:20px;font-weight:700;margin:3px 0">{value_html}</div>'
            f'<div style="font-size:12px;opacity:.6">{sub}</div>',
            unsafe_allow_html=True)


# Regime what-if — on the MAIN page (was buried in the sidebar), sitting right
# above the Market-regime tile it drives. Purely a DISPLAY overlay; it never
# re-runs the engine or mutates stored signals.
assumed_regime = None   # what-if control removed; overlay logic below unchanged


# Under an assumed risk-on regime, reverse ONLY the risk-off regime gate: a Watch
# whose reason cites that exact gate was a technical Buy the engine downgraded for
# regime alone, so it surfaces as a Buy. The phrase match is exact, so confluence/
# chase/earnings/rr downgrades are never touched. Buy, never Strong Buy (pre-gate
# tier unknown — take the conservative one).
def _display_signal(sig, reason):
    return sig


latest["display_signal"] = [
    _display_signal(s, mr)
    for s, mr in zip(latest["signal"], latest["main_reason"])]
_whatif_active = bool((latest["display_signal"] != latest["signal"]).any())
buys = latest[latest["display_signal"].isin(["Strong Buy", "Buy"])]
exits = latest[latest["display_signal"] == "Exit"]

st.markdown(
    f'<div style="display:flex;gap:20px;align-items:center;font-size:13px;'
    f'opacity:.8;margin:2px 0 10px">{regime_pill(regime)}'
    f'<span><b>{len(buys)}</b> buys · <b>{len(exits)}</b> exits</span>'
    f'<span>updated {_last_updated_html}</span></div>',
    unsafe_allow_html=True)

# Staleness banner — louder than the tile, only shown when data is past amber.
if not _market_live:
    st.caption("Market closed. Intraday checks resume during trading hours; swing cards show their analysis date.")
elif _stale_level != "fresh":
    # Now a statement about the ENGINE, not about the numbers: it fires when no
    # cycle has completed recently, which is the condition that actually needs
    # acting on. Signals describing the previous session is the contract and
    # must never be reported as a fault.
    _what = "The engine has not completed a cycle"
    if _stale_level == "red":
        st.error(f"⚠ {_what} for **{_age_hours:.1f} hours** (over the {_red}h "
                 "threshold). The loop may have stopped — check the Actions run "
                 "before acting on anything below.")
    else:
        st.warning(f"⏳ {_what} for **{_age_hours:.1f} hours** — past the "
                   f"{_amber}h threshold. Verify quotes manually before acting.")
def _why_not_buy_section():
    why = latest[(latest["final_score"] >= config.SIGNAL_THRESHOLDS["buy"]) &
                 (~latest["signal"].isin(["Strong Buy", "Buy"]))]
    if why.empty:
        return
    st.caption("These scored in Buy range but a safety rule held them back. "
               "No need to dig — the reason is shown.")
    for _, r in why.iterrows():
        st.markdown(
            f'{sig_pill(r["signal"])} &nbsp;**{r["symbol"]}** '
            f'(score {r["final_score"]:.0f}) — '
            f'<span style="opacity:.8">{why_not_buy(r["main_reason"])}</span>'
            f'<br>{news_line(r["symbol"])}',
            unsafe_allow_html=True)


def _early_watch_section():
    ew = latest[latest.get("early_watch").fillna(0) == 1] if "early_watch" in latest.columns \
        else latest.iloc[0:0]
    if ew.empty:
        st.caption("No early-watch names right now. This tier only fires on real "
                   "money-flow build-up (CMF) below the Buy band.")
        return
    st.caption("Lead-time tier: money flow building BEFORE the score confirms. "
               "NOT a buy signal — it exists so a move can be prepared for "
               "instead of chased. Graded on the 7-day horizon; treat as "
               "unproven until that history accumulates.")
    for _, r in ew.sort_values("cmf", ascending=False).iterrows():
        st.markdown(
            f'<span style="background:rgba(122,162,255,0.16);color:#7aa2ff;'
            f'padding:2px 8px;border-radius:6px;font-size:12px;font-weight:700">'
            f'🔭 EARLY</span> &nbsp;**{r["symbol"]}** · price {fmt(r["price"])} · '
            f'score {fmt(r["final_score"], 0)} · CMF {fmt(r.get("cmf"), 2)} · '
            f'RS {fmt(r.get("relative_strength"), 0)}'
            f'<br><span style="opacity:.75;font-size:13px">'
            f'{r.get("early_reason") or ""}</span>'
            f'<br>{news_line(r["symbol"])}',
            unsafe_allow_html=True)


# The trading desk shows opportunities only. Supporting tools live in their own tabs.
@st.cache_resource(ttl=3600, show_spinner="Loading the latest engine data...")
def live_database():
    """Swap in the engine's current database (see remote_data). Cached per
    server for an hour; on any failure the bundled database is kept."""
    import remote_data
    return remote_data.refresh_db()


# A deployment that bootstraps its own runtime database (psx-dashboard's
# runtime_bootstrap sets PSX_DB_PATH) already has fresh data: skip the second
# download.
_db_path, _db_from = (None, "bootstrapped") if os.environ.get("PSX_DB_PATH") else live_database()
if _db_path:
    config.DB_PATH = _db_path   # cache hit on a rerun: re-point this process too

(tab_desk, tab_watch, tab_edge, tab_stock, tab_hist,
 tab_news, tab_reports, tab_research) = st.tabs(
    ["Trading desk", "📋 Watchlist", "🧪 Past results", "🔍 Stock detail",
     "📈 History", "📰 News", "📋 Reports", "🕯 Research"])

with tab_research:
    import research_desk
    research_desk.show(st)
    st.divider()
    import research_panel
    research_panel.show(st, database=config.DB_PATH)

with tab_desk:
    st.info("Guru research: open the Research tab for the 15-stock intraday, swing and long-term evidence desk. Freshness and missing inputs are shown before any conditional plan.")
    import intraday_momentum
    import opportunity_cards
    import news_desk
    _raw_news, _raw_where = freshest_news()
    st.markdown(news_desk.desk_html(_raw_news, _raw_where, opportunity_cards.reviewers()),
                unsafe_allow_html=True)
    intraday_momentum.show(st, details=False)
    st.subheader("Swing opportunities")
    action = latest[latest["display_signal"].isin(["Strong Buy", "Buy", "Exit"])]
    if action.empty:
        st.markdown('<div class="desk-note">No Buy or Exit signals currently qualify.</div>', unsafe_allow_html=True)
    else:
        opportunity_cards.show_swing(st, action.to_dict("records"), details=False, raw=_raw_news)
    st.caption("Each card lists that stock's own latest headlines (unrated) and the Claude and Codex reviews. Full evidence is in News; trade details are in Stock detail.")

with tab_watch:
    st.subheader("Latest intraday observations")
    try:
        _capture = json.loads(intraday_momentum.PATH.read_text(encoding="utf-8"))
        _observations = _capture.get("observations", [])
        st.caption("Captured " + str(_capture.get("checked_at", "unknown")) + " · observations, not independent trade recommendations")
        if _observations:
            st.dataframe([{"Stock": r["symbol"], "Captured state": r.get("state"), "Last price": r.get("price"),
                           "Since open %": r.get("since_open_pct"), "Last 15 min %": r.get("recent_pct"),
                           "Why": r.get("reason"), "Last trade": r.get("last_trade")} for r in _observations], hide_index=True)
    except (OSError, ValueError, TypeError):
        st.caption("No intraday capture available.")
    st.subheader("Swing watchlist")
    st.caption("Full ranking — colour-coded. Sort by clicking a column header.")
    show = latest[["symbol", "final_score", "relative_strength", "signal",
                   "risk_level", "confidence", "price", "stop_loss", "target1",
                   "buy_zone_low", "buy_zone_high",
                   "data_quality", "shariah_status"]].copy()
    show["buy_zone"] = [f"{lo:.2f}–{hi:.2f}" if pd.notna(lo) and pd.notna(hi) else "—"
                        for lo, hi in zip(show["buy_zone_low"], show["buy_zone_high"])]
    show = show.drop(columns=["buy_zone_low", "buy_zone_high"])
    show["news"] = [news_cell(s) for s in show["symbol"]]
    show.columns = ["Symbol", "Score", "Market strength", "Signal", "Risk", "Quality",
                    "Price", "Stop", "Target", "Data", "Shariah", "Buy-zone",
                    "News"]

    def _sig_css(v):
        c = NEON_SIG.get(v)
        if not c:
            return ""
        r, g, b = _hex_rgb(c)
        return f"background-color:rgba({r},{g},{b},0.16);color:{c};font-weight:700"

    def _risk_css(v):
        c = NEON_RISK.get(v)
        if not c:
            return ""
        r, g, b = _hex_rgb(c)
        return f"background-color:rgba({r},{g},{b},0.16);color:{c};font-weight:700"

    styled = (show.style
              .map(_sig_css, subset=["Signal"])
              .map(_risk_css, subset=["Risk"])
              .format({"Score": "{:.1f}", "Market strength": "{:.0f}", "Quality": "{:.0f}",
                       "Price": "{:.2f}", "Stop": "{:.2f}", "Target": "{:.2f}"},
                      na_rep="—"))
    st.dataframe(styled, width="stretch", hide_index=True, height=560)

    # The two panels the trim orphaned: their functions survived with no caller,
    # so they rendered nowhere. They belong with the full ranking rather than on
    # the front page, which is what pushed them off it.
    st.divider()
    st.subheader("⚠ High score, but NOT a Buy — here's why")
    _why_not_buy_section()
    st.divider()
    st.subheader("🔭 Early watch — building before the Buy band")
    _early_watch_section()

with tab_edge:
    import trading_review
    trading_review.show(st, rows)
    st.subheader("How past Buy signals performed")
    st.caption("Checks all tracked stocks against verified daily prices. This "
               "measures software behaviour on past bars — it is not a forecast "
               "and not a profitability claim.")
    if st.button("Check past signals for all stocks"):
        with st.spinner("Checking past prices for all stocks..."):
            res = bt_portfolio(os.stat(config.DB_PATH).st_mtime_ns)
        import history_view
        history_view.show(st, res)

with tab_stock:
    import depth_analysis
    depth_analysis.show(st)
    sym = st.selectbox("Stock", config.STOCKS)
    r = db.last_run(sym)
    if r:
        from history_view import explain_run
        r = dict(r)
        r['main_reason'], r['main_risk'] = explain_run(r)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Signal", r["signal"], f"{fmt(r['confidence'], 0)}/100 quality")
        c2.metric("Final score", fmt(r["final_score"], 1))
        c3.metric("Strength versus market", fmt(r.get("relative_strength"), 0))
        c4.metric("Price", fmt(r["price"]))
        c5.metric("Risk", r["risk_level"])
        st.write("**Why:**", r["main_reason"])
        st.write("**Main risk:**", r["main_risk"])
        st.write("**Shariah:**", r["shariah_status"], " · **Market direction:**",
                 {'risk-on': 'Rising', 'risk-off': 'Falling'}.get(r.get("market_regime"), 'Unknown'))
        # The news read for this symbol, including the story so far when there
        # is one -- silent when this is a first sighting.
        st.markdown(news_line(sym), unsafe_allow_html=True)
        try:
            import news_memory
            _thread = news_memory.thread_summary(sym)
            if _thread:
                with st.expander("📰 Story so far — every earlier read on this stock"):
                    st.code(_thread, language=None)
        except Exception:
            pass
        _news_window(sym, news_feed.get(sym))

    # Banked bars FIRST. daily_ohlc is the same completed-session history the
    # strategy reads, it is already local, and it cannot stall. The live EOD
    # call is only a top-up: when the feed is down -- which has happened twice
    # -- it used to block the whole page here, so the tabs below never painted.
    eod, meta = None, {}
    _bars = db.get_daily_ohlc(sym, limit=config.FEATURE_HISTORY_LIMIT)
    if _bars:
        eod = pd.DataFrame(_bars)[["date", "close", "volume"]]
        meta = {"source": "banked daily bars (completed sessions)",
                "as_of": eod["date"].max()}
    else:
        try:
            eod, meta = data_fetcher.fetch_eod(sym)
        except Exception as exc:
            eod, meta = None, {"warning": f"No banked bars and the live feed "
                                          f"is unreachable: {exc}"}
    if eod is not None:
        eod = eod.sort_values('date').tail(config.FEATURE_HISTORY_LIMIT)
        st.caption(f"Source: {meta['source']} (as of {meta['as_of']})")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eod["date"], y=eod["close"], name="Close",
                                 line=dict(color=NEON["cyan"], width=2)))
        fig.add_trace(go.Scatter(x=eod["date"], y=eod["close"].ewm(span=20).mean(),
                                 name="Short price trend (20 days)",
                                 line=dict(color=NEON["amber"], dash="dot")))
        fig.add_trace(go.Scatter(x=eod["date"], y=eod["close"].ewm(span=40).mean(),
                                 name="Slower price trend (40 days)",
                                 line=dict(color=NEON["violet"], dash="dash")))
        if r:
            for lvl, nm, clr in ((r["support"], "Support", NEON["green"]),
                                 (r["resistance"], "Resistance", NEON["red"]),
                                 (r["stop_loss"], "Stop", NEON["red"])):
                if lvl:
                    fig.add_hline(y=lvl, line_dash="dot", line_color=clr,
                                  annotation_text=nm,
                                  annotation_font_color=clr)
        fig.update_layout(title=f"{sym} — price & moving averages")
        st.plotly_chart(neon_fig(fig, height=420), width="stretch")
        volf = go.Figure(go.Bar(x=eod["date"], y=eod["volume"], name="Volume",
                                marker=dict(color="rgba(0,229,255,0.5)")))
        volf.update_layout(title="Volume")
        st.plotly_chart(neon_fig(volf, height=220), width="stretch")
    else:
        st.error(meta.get("warning", "No price data."))

    with st.expander("How past Buy signals performed"):
        if st.button(f"Check past signals for {sym}", key="bt_one"):
            res = bt_symbol(sym, os.stat(config.DB_PATH).st_mtime_ns)
            import history_view
            history_view.show(st, res)

with tab_hist:
    sym = st.selectbox("Stock ", config.STOCKS, key="hist")
    hist = pd.DataFrame(db.run_history(sym, 300))
    if len(hist):
        hist["run_time"] = pd.to_datetime(hist["run_time"], utc=True, format="mixed")
        cols = [c for c in ["final_score", "technical_score", "relative_strength"]
                if c in hist.columns]
        st.line_chart(hist.set_index("run_time")[cols])
        st.caption("The score is a guide, not a chance of profit. Older results "
                   "compare the stock with the market.")
        st.subheader("Signal history")
        st.dataframe(hist[["run_time", "signal", "confidence", "price", "outcome"]],
                     width="stretch", hide_index=True)
    else:
        st.info("No run history stored for this stock yet.")

with tab_news:
    import news_review_panel
    news_review_panel.show(st)
    st.caption("Every headline gathered from approved publishers, banked and "
               "kept. Unrated and unscored — the rated read is the panel at "
               "the top. This is the record a new story is judged against.")
    # LIVE is deliberately a different SOURCE, not a shorter window. The engine
    # loop refetches news_raw_24h.json every cycle (~15 min) and that file is
    # ~100 KB, so it is committed every time; the database is 48 MB and is
    # committed only when signals move. Reading the file here is what makes a
    # breaking story visible within a cycle instead of waiting for a signal
    # change to carry the database along with it.
    _win = st.radio("Window", ["live", 3, 7, 30, 365], index=0, horizontal=True,
                    format_func=lambda d: {"live": "Latest (24h, live file)",
                                           3: "3 days", 7: "7 days",
                                           30: "30 days", 365: "1 year"}[d],
                    key="news_win")
    if _win == "live":
        _items = []
        try:
            _blob, _where = freshest_news()
            if not _blob:
                raise ValueError("no news file available")
            _fetched = _blob.get("fetched_at") or ""
            for _it in _blob.get("items") or []:
                _sym = _it.get("symbol") or ""
                _items.append({"fetched_at": _it.get("published") or _fetched,
                               "source": _it.get("source") or "?",
                               "title": _it.get("title") or "",
                               "link": _it.get("url") or _it.get("link") or "",
                               "symbols": "" if _sym.startswith("_") else _sym})
            _items.sort(key=lambda x: str(x["fetched_at"]), reverse=True)
            _mins = None
            try:
                from datetime import datetime as _dt, timezone as _tz
                _t = _dt.fromisoformat(str(_fetched))
                if _t.tzinfo is None:
                    _t = _t.replace(tzinfo=_tz.utc)
                _mins = (_dt.now(_tz.utc) - _t).total_seconds() / 60
            except (ValueError, TypeError):
                pass
            _agetxt = ""
            if _mins is not None:
                _agetxt = (f" · fetched {_mins:.0f} min ago" if _mins < 90
                           else f" · fetched {_mins/60:.1f}h ago — the loop may "
                                f"not be running")
            st.caption(f"Straight from the live fetch file ({_where}) · "
                       f"collected {str(_fetched)[:16]}{_agetxt}")
        except (OSError, ValueError) as _exc:
            st.warning(f"Live news file unreadable ({_exc}). Falling back to the "
                       "banked record — pick a day window above.")
            _items = []
    else:
        _items = db.recent_news(_win * 24)
    _total = len(_items)
    if not _items:
        # Distinguish "nothing published" from "nothing ingested" -- the second
        # is a broken pipeline and used to look exactly like the first.
        with db.conn() as _c:
            _last = _c.execute("SELECT MAX(fetched_at) FROM news").fetchone()[0]
        if _win == "live":
            st.warning("The live fetch file holds no items. The newest banked "
                       f"headline is from {str(_last)[:16]}."
                       if _last else "No news available from either source.")
        elif _last:
            st.warning(f"No headlines in the last {_win} day(s). The newest "
                       f"banked headline is from {str(_last)[:16]} — if that is "
                       f"old, the news fetch has stopped running.")
        else:
            st.warning("No headlines banked at all. The news fetch is not "
                       "reaching the database.")
    else:
        st.caption(f"{_total} headlines · newest {str(_items[0]['fetched_at'])[:16]}")
        for n in _items[:120]:
            tag = f" `[{n['symbols']}]`" if n["symbols"] else ""
            _link = n["link"] or ""
            _title = f"[{n['title']}]({_link})" if _link else n["title"]
            st.markdown(f"- `{str(n['fetched_at'])[:10]}` **{n['source']}** — "
                        f"{_title}{tag}")
        if _total > 120:
            st.caption(f"Showing the newest 120 of {_total}.")

with tab_reports:
    import short_horizon_panel
    short_horizon_panel.show(st)
    import upward_candidates
    st.subheader("Stocks with a rising trend")
    _upward = upward_candidates.current()
    if _upward:
        st.dataframe(pd.DataFrame(_upward), hide_index=True)
    else:
        st.caption("No stocks currently meet every rising-trend check.")
    if os.path.isdir(config.REPORT_DIR):
        files = sorted(os.listdir(config.REPORT_DIR), reverse=True)[:10]
        pick = st.selectbox("Saved reports", files) if files else None
        if pick:
            with open(os.path.join(config.REPORT_DIR, pick), encoding="utf-8") as f:
                st.markdown(f.read())
        elif not files:
            st.info("No reports saved yet.")
    else:
        st.info("No reports saved yet.")
