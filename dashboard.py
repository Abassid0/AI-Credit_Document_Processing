"""
dashboard.py — Credit application analytics dashboard (Flask + Plotly).

Run with:
    python dashboard.py
Then open http://localhost:5050 in your browser.

Shows live metrics from Supabase. Falls back to synthetic sample data
when Supabase is not configured so the dashboard is always demo-ready.
"""
import json
import random
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from flask import Flask, Response

# ---------------------------------------------------------------------------
# Bootstrap config / storage
# ---------------------------------------------------------------------------

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import config  # noqa: E402  (must come after load_dotenv)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Sample data (used when Supabase is not configured)
# ---------------------------------------------------------------------------

DEMO_FLAG_TYPES = [
    "name_mismatch",
    "dob_mismatch",
    "address_mismatch",
    "stale_bank_statement",
    "missing_field",
    "bvn_format_invalid",
    "nin_format_invalid",
    "low_confidence_extraction",
]
CHANNELS = ["telegram", "sms", "whatsapp", "email", "none"]
GENDERS  = ["Male", "Female", "Other", "Prefer not to say"]
DEMO_PRODUCTS = [
    ("MFB-SAL-001", "Salary Advance"),
    ("MFB-GRP-001", "Group Loan"),
    ("MFB-SME-001", "SME Business Loan"),
    ("FIN-INS-001", "Instant Personal Loan"),
    ("FIN-BNPL-001", "Buy Now Pay Later"),
    ("BNK-SAL-001", "Personal Salary Loan"),
    ("BNK-TRM-001", "SME Term Loan"),
]


def _synthetic_applications(n: int = 60) -> list[dict]:
    rng = random.Random(42)
    base = datetime.now(timezone.utc) - timedelta(days=30)
    rows = []
    for _ in range(n):
        created = base + timedelta(days=rng.uniform(0, 30), hours=rng.uniform(0, 24))
        completeness = min(100, max(0, round(rng.gauss(82, 14), 1)))
        ready = completeness >= 75 and rng.random() > 0.25
        num_flags = rng.choices([0, 1, 2, 3], weights=[45, 30, 15, 10])[0]
        flags = [
            {"type": rng.choice(DEMO_FLAG_TYPES), "severity": rng.choice(["blocker", "warning"])}
            for _ in range(num_flags)
        ]
        channel = rng.choices(CHANNELS, weights=[65, 15, 8, 7, 5])[0]
        product = rng.choice(DEMO_PRODUCTS)
        rows.append({
            "reference_number":      f"CRB-{created.strftime('%Y%m%d')}-{secrets.token_hex(2)}",
            "officer_code":          rng.choice(["OFC001", "OFC002", None, None]),
            "declared_gender":       rng.choice(GENDERS),
            "completeness_pct":      completeness,
            "ready_for_underwriting": ready,
            "flags":                 flags,
            "turnaround_seconds":    round(rng.gauss(18, 5), 2),
            "notification_channel":  channel,
            "notification_status":   "delivered" if channel != "none" else "failed",
            "created_at":            created.isoformat(),
            "processed_at":          (created + timedelta(seconds=rng.uniform(12, 35))).isoformat(),
            "status":                "processed",
            "product_code":          product[0],
            "product_name":          product[1],
        })
    return rows


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    """
    Returns (apps_df, audit_df, is_demo).
    Tries Supabase; falls back to synthetic sample data on failure.
    """
    if config.SUPABASE_URL and config.SUPABASE_KEY:
        try:
            from storage import list_applications, list_audit_events
            rows = list_applications(limit=500)
            audit_rows = list_audit_events(limit=200)
            if rows:
                return pd.DataFrame(rows), pd.DataFrame(audit_rows or []), False
        except Exception:
            pass
    return pd.DataFrame(_synthetic_applications(60)), pd.DataFrame(), True


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

ACCENT  = "#4318FF"
GREEN   = "#01B574"
RED     = "#EE5D50"
ORANGE  = "#FFB547"
INFO    = "#2B77E7"
TEAL    = "#0DCAF0"
WA      = "#25D366"

CHART_COLORS = [ACCENT, GREEN, ORANGE, RED, INFO, TEAL, "#7B61FF", "#E667AF"]

PLOTLY_THEME = dict(
    template="plotly_white",
    margin=dict(l=10, r=10, t=30, b=10),
    font=dict(family="Plus Jakarta Sans, Inter, system-ui, sans-serif", size=12, color="#2B3674"),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
)


def fig_to_json(fig) -> str:
    return pio.to_json(fig)


def chart_volume(df: pd.DataFrame) -> str:
    daily = df.groupby("date").size().reset_index(name="count").sort_values("date")
    fig = px.area(daily, x="date", y="count",
                  color_discrete_sequence=[ACCENT],
                  labels={"date": "", "count": "Applications"},
                  title="Applications per day")
    fig.update_traces(line_width=2.5, fillcolor="rgba(67,24,255,0.08)",
                      line_shape="spline")
    fig.update_layout(**PLOTLY_THEME, height=280)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(163,174,208,0.15)")
    return fig_to_json(fig)


def chart_ready(processed: pd.DataFrame) -> str:
    ready_yes = int(processed["ready_for_underwriting"].sum())
    ready_no  = len(processed) - ready_yes
    fig = go.Figure(go.Pie(
        labels=["Ready", "Not ready"],
        values=[ready_yes, ready_no],
        hole=0.65,
        marker_colors=[GREEN, RED],
        textinfo="percent+label",
        textfont_size=13,
        pull=[0.02, 0],
    ))
    fig.update_layout(**PLOTLY_THEME, height=280, title="Readiness split",
                      showlegend=False)
    return fig_to_json(fig)


def _shorten_flag(raw: str) -> str:
    """Turn verbose flag strings into compact labels."""
    raw = raw.strip()
    for noise in ("field not found on document.", "field not found on document",
                  "field not found on doc", "field not found"):
        raw = raw.replace(noise, "").strip().rstrip(".:,")
    parts = raw.rsplit(".", 1)
    if len(parts) == 2:
        doc, field = parts
        doc = doc.replace("_", " ").title()
        field = field.replace("_", " ").title()
        return f"{doc} → {field}"
    return raw.replace("_", " ").title()[:40]


def chart_flags(processed: pd.DataFrame) -> str:
    flag_counts: dict[str, int] = {}
    for flags_val in processed["flags"].dropna():
        items = flags_val if isinstance(flags_val, list) else []
        for f in items:
            raw = (f.get("type") or f.get("message") or "unknown") if isinstance(f, dict) else str(f)
            label = _shorten_flag(raw)
            flag_counts[label] = flag_counts.get(label, 0) + 1
    if not flag_counts:
        fig = go.Figure()
        fig.add_annotation(text="No flags raised", x=0.5, y=0.5, showarrow=False,
                           font_size=16, font_color="#A3AED0")
        fig.update_layout(**PLOTLY_THEME, height=280, title="Common flags")
        return fig_to_json(fig)
    flag_df = (
        pd.DataFrame(list(flag_counts.items()), columns=["flag", "count"])
        .sort_values("count", ascending=True)
        .tail(8)
    )
    fig = px.bar(flag_df, x="count", y="flag", orientation="h",
                 color_discrete_sequence=[RED],
                 labels={"count": "Occurrences", "flag": ""},
                 title="Most common flags")
    fig.update_layout(**PLOTLY_THEME, height=300)
    fig.update_xaxes(showgrid=True, gridcolor="rgba(163,174,208,0.15)")
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11))
    return fig_to_json(fig)


def chart_channels(processed: pd.DataFrame) -> str:
    notified = processed[processed["notification_channel"].notna()]
    if notified.empty:
        fig = go.Figure()
        fig.add_annotation(text="No notifications sent", x=0.5, y=0.5, showarrow=False,
                           font_size=16, font_color="#A3AED0")
        fig.update_layout(**PLOTLY_THEME, height=280, title="Notification channels")
        return fig_to_json(fig)
    ch_counts = notified["notification_channel"].value_counts().reset_index()
    ch_counts.columns = ["channel", "count"]
    color_map = {
        "telegram": "#0088CC", "sms": ORANGE,
        "whatsapp": WA, "email": INFO, "none": "#BBBBBB",
    }
    fig = px.bar(ch_counts, x="channel", y="count", color="channel",
                 color_discrete_map=color_map,
                 labels={"channel": "", "count": "Sent"},
                 title="Notification channels")
    fig.update_layout(**PLOTLY_THEME, height=280, showlegend=False)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(163,174,208,0.15)")
    fig.update_traces(marker_line_width=0, marker_cornerradius=6)
    return fig_to_json(fig)


def chart_completeness(processed: pd.DataFrame) -> str:
    fig = px.histogram(processed, x="completeness_pct", nbins=20,
                       color_discrete_sequence=[ACCENT],
                       labels={"completeness_pct": "Completeness (%)", "count": ""},
                       title="Completeness distribution")
    fig.add_vline(x=75, line_dash="dash", line_color=RED, line_width=1.5,
                  annotation_text="75% threshold", annotation_position="top right",
                  annotation_font_color=RED)
    fig.update_layout(**PLOTLY_THEME, height=280)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(163,174,208,0.15)")
    fig.update_traces(marker_line_width=0, marker_cornerradius=4)
    return fig_to_json(fig)


def chart_gender(processed: pd.DataFrame) -> str:
    if "declared_gender" not in processed.columns or processed["declared_gender"].dropna().empty:
        fig = go.Figure()
        fig.add_annotation(text="No gender data", x=0.5, y=0.5, showarrow=False,
                           font_size=16, font_color="#A3AED0")
        fig.update_layout(**PLOTLY_THEME, height=280, title="Gender distribution")
        return fig_to_json(fig)
    gender_counts = processed["declared_gender"].value_counts().reset_index()
    gender_counts.columns = ["gender", "count"]
    colors = [ACCENT, GREEN, ORANGE, "#7B61FF"]
    fig = go.Figure(go.Pie(
        labels=gender_counts["gender"],
        values=gender_counts["count"],
        hole=0.65,
        marker_colors=colors[:len(gender_counts)],
        textinfo="percent+label",
        textfont_size=12,
    ))
    fig.update_layout(**PLOTLY_THEME, height=280, title="Gender distribution",
                      showlegend=False)
    return fig_to_json(fig)


def chart_products(processed: pd.DataFrame) -> str:
    if "product_name" not in processed.columns or processed["product_name"].dropna().empty:
        fig = go.Figure()
        fig.add_annotation(text="No product data yet", x=0.5, y=0.5, showarrow=False,
                           font_size=16, font_color="#A3AED0")
        fig.update_layout(**PLOTLY_THEME, height=280, title="Applications by product")
        return fig_to_json(fig)
    prod_counts = processed["product_name"].value_counts().reset_index()
    prod_counts.columns = ["product", "count"]
    fig = px.bar(prod_counts, x="count", y="product", orientation="h",
                 color_discrete_sequence=[ACCENT],
                 labels={"count": "Applications", "product": ""},
                 title="Applications by product")
    fig.update_layout(**PLOTLY_THEME, height=max(280, len(prod_counts) * 35 + 80))
    fig.update_xaxes(showgrid=True, gridcolor="rgba(163,174,208,0.15)")
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11))
    fig.update_traces(marker_line_width=0, marker_cornerradius=6)
    return fig_to_json(fig)


def chart_product_readiness(processed: pd.DataFrame) -> str:
    if "product_name" not in processed.columns or processed["product_name"].dropna().empty:
        fig = go.Figure()
        fig.add_annotation(text="No product data yet", x=0.5, y=0.5, showarrow=False,
                           font_size=16, font_color="#A3AED0")
        fig.update_layout(**PLOTLY_THEME, height=280, title="Readiness by product")
        return fig_to_json(fig)
    grp = processed.groupby("product_name").agg(
        ready=("ready_for_underwriting", "sum"),
        total=("ready_for_underwriting", "count"),
    ).reset_index()
    grp["not_ready"] = grp["total"] - grp["ready"]
    grp = grp.sort_values("total", ascending=True)
    fig = go.Figure()
    fig.add_trace(go.Bar(y=grp["product_name"], x=grp["ready"], name="Ready",
                         orientation="h", marker_color=GREEN,
                         marker_cornerradius=4))
    fig.add_trace(go.Bar(y=grp["product_name"], x=grp["not_ready"], name="Not ready",
                         orientation="h", marker_color=RED,
                         marker_cornerradius=4))
    fig.update_layout(**PLOTLY_THEME, height=max(280, len(grp) * 35 + 80),
                      title="Readiness by product", barmode="stack",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                  xanchor="right", x=1, font_size=11))
    fig.update_xaxes(showgrid=True, gridcolor="rgba(163,174,208,0.15)")
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11))
    return fig_to_json(fig)


def chart_institution_type(processed: pd.DataFrame) -> str:
    if "product_code" not in processed.columns or processed["product_code"].dropna().empty:
        fig = go.Figure()
        fig.add_annotation(text="No institution data yet", x=0.5, y=0.5, showarrow=False,
                           font_size=16, font_color="#A3AED0")
        fig.update_layout(**PLOTLY_THEME, height=280, title="By institution type")
        return fig_to_json(fig)
    def _inst_type(code):
        if not code or not isinstance(code, str):
            return "Unknown"
        prefix = code.split("-")[0].upper()
        return {"MFB": "Microfinance", "FIN": "Fintech", "BNK": "Bank"}.get(prefix, "Other")
    processed = processed.copy()
    processed["inst_type"] = processed["product_code"].apply(_inst_type)
    inst_counts = processed["inst_type"].value_counts().reset_index()
    inst_counts.columns = ["type", "count"]
    colors = {"Microfinance": ORANGE, "Fintech": ACCENT, "Bank": GREEN, "Other": "#A3AED0", "Unknown": "#A3AED0"}
    fig = go.Figure(go.Pie(
        labels=inst_counts["type"],
        values=inst_counts["count"],
        hole=0.65,
        marker_colors=[colors.get(t, "#A3AED0") for t in inst_counts["type"]],
        textinfo="percent+label",
        textfont_size=12,
    ))
    fig.update_layout(**PLOTLY_THEME, height=280, title="By institution type",
                      showlegend=False)
    return fig_to_json(fig)


def chart_turnaround(df: pd.DataFrame) -> str:
    daily = df.groupby("date")["turnaround_seconds"].mean().reset_index().sort_values("date")
    fig = px.line(daily, x="date", y="turnaround_seconds",
                  color_discrete_sequence=[GREEN],
                  labels={"date": "", "turnaround_seconds": "Avg seconds"},
                  title="Processing time trend")
    fig.update_traces(line_width=2.5, mode="lines+markers",
                      marker=dict(size=6, color=GREEN),
                      line_shape="spline")
    fig.update_layout(**PLOTLY_THEME, height=280)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(163,174,208,0.15)")
    return fig_to_json(fig)


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CreditBot Analytics</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:         #F4F7FE;
    --card:       #FFFFFF;
    --border:     #E9EDF7;
    --text:       #1B2559;
    --text-sec:   #2B3674;
    --muted:      #A3AED0;
    --accent:     #4318FF;
    --accent-bg:  rgba(67,24,255,0.06);
    --green:      #01B574;
    --green-bg:   rgba(1,181,116,0.08);
    --red:        #EE5D50;
    --red-bg:     rgba(238,93,80,0.08);
    --orange:     #FFB547;
    --orange-bg:  rgba(255,181,71,0.08);
    --info:       #2B77E7;
    --info-bg:    rgba(43,119,231,0.08);
    --sidebar:    #1B2559;
    --shadow-sm:  0 1px 3px rgba(27,37,89,0.04);
    --shadow-md:  0 4px 14px rgba(27,37,89,0.07);
    --shadow-lg:  0 8px 28px rgba(27,37,89,0.10);
    --radius:     16px;
    --radius-sm:  10px;
    --font:       'Plus Jakarta Sans', 'Inter', system-ui, -apple-system, sans-serif;
    --font-body:  'Inter', system-ui, -apple-system, sans-serif;
    --font-mono:  'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  }

  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg:         #0B1437;
      --card:       #111C44;
      --border:     #1B254B;
      --text:       #FFFFFF;
      --text-sec:   #E2E8F0;
      --muted:      #A3AED0;
      --accent-bg:  rgba(67,24,255,0.15);
      --green-bg:   rgba(1,181,116,0.15);
      --red-bg:     rgba(238,93,80,0.15);
      --orange-bg:  rgba(255,181,71,0.12);
      --info-bg:    rgba(43,119,231,0.12);
      --shadow-sm:  0 1px 3px rgba(0,0,0,0.2);
      --shadow-md:  0 4px 14px rgba(0,0,0,0.25);
      --shadow-lg:  0 8px 28px rgba(0,0,0,0.3);
    }
  }
  :root[data-theme="dark"] {
    --bg:         #0B1437;
    --card:       #111C44;
    --border:     #1B254B;
    --text:       #FFFFFF;
    --text-sec:   #E2E8F0;
    --muted:      #A3AED0;
    --accent-bg:  rgba(67,24,255,0.15);
    --green-bg:   rgba(1,181,116,0.15);
    --red-bg:     rgba(238,93,80,0.15);
    --orange-bg:  rgba(255,181,71,0.12);
    --info-bg:    rgba(43,119,231,0.12);
    --shadow-sm:  0 1px 3px rgba(0,0,0,0.2);
    --shadow-md:  0 4px 14px rgba(0,0,0,0.25);
    --shadow-lg:  0 8px 28px rgba(0,0,0,0.3);
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 14px;
    line-height: 1.5;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }

  /* ── Sidebar ──────────────────────────────────────────────────────── */
  .sidebar {
    position: fixed; top: 0; left: 0; bottom: 0;
    width: 260px;
    background: linear-gradient(180deg, #1B2559 0%, #111C44 100%);
    display: flex; flex-direction: column;
    z-index: 100;
    transition: transform 0.3s cubic-bezier(.4,0,.2,1);
  }
  .sidebar-brand {
    padding: 1.75rem 1.5rem 1.5rem;
    display: flex; align-items: center; gap: 0.75rem;
  }
  .sidebar-brand .logo {
    width: 40px; height: 40px;
    background: var(--accent);
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.1rem; color: #fff; font-weight: 800;
  }
  .sidebar-brand .brand-text {
    color: #FFFFFF; font-weight: 700; font-size: 1.1rem;
    letter-spacing: -0.01em;
  }
  .sidebar-brand .brand-sub {
    color: rgba(255,255,255,0.4); font-size: 0.7rem;
    font-weight: 500; margin-top: 2px;
  }
  .sidebar-divider {
    height: 1px; background: rgba(255,255,255,0.06);
    margin: 0 1.25rem 0.75rem;
  }
  .sidebar-nav { flex: 1; padding: 0 0.75rem; }
  .sidebar-nav a {
    display: flex; align-items: center; gap: 0.8rem;
    padding: 0.7rem 0.85rem;
    border-radius: 12px;
    color: rgba(255,255,255,0.5);
    text-decoration: none;
    font-size: 0.85rem; font-weight: 500;
    transition: all 0.2s;
    margin-bottom: 2px;
    position: relative;
  }
  .sidebar-nav a:hover { background: rgba(255,255,255,0.06); color: rgba(255,255,255,0.8); }
  .sidebar-nav a.active {
    background: rgba(255,255,255,0.1);
    color: #FFFFFF; font-weight: 600;
  }
  .sidebar-nav a.active::before {
    content: ''; position: absolute; left: 0; top: 50%; transform: translateY(-50%);
    width: 4px; height: 24px; border-radius: 0 4px 4px 0;
    background: var(--accent);
  }
  .sidebar-nav svg { width: 20px; height: 20px; flex-shrink: 0; opacity: 0.7; }
  .sidebar-nav a.active svg { opacity: 1; }
  .sidebar-footer {
    padding: 1rem 1.5rem;
    border-top: 1px solid rgba(255,255,255,0.06);
  }
  .sidebar-footer p {
    color: rgba(255,255,255,0.25); font-size: 0.68rem; line-height: 1.5;
  }

  /* ── Main ─────────────────────────────────────────────────────────── */
  .main-wrap {
    margin-left: 260px;
    min-height: 100vh;
    display: flex; flex-direction: column;
  }

  /* ── Header ───────────────────────────────────────────────────────── */
  .top-header {
    position: sticky; top: 0; z-index: 50;
    background: var(--bg);
    padding: 1.1rem 2.25rem;
    display: flex; align-items: center; justify-content: space-between;
    gap: 1rem;
    border-bottom: 1px solid var(--border);
    backdrop-filter: blur(12px);
  }
  .top-header h1 {
    font-size: 1.35rem; font-weight: 800; color: var(--text);
    letter-spacing: -0.02em;
  }
  .top-header .subtitle {
    font-size: 0.78rem; color: var(--muted); font-weight: 500;
    margin-top: 2px;
  }
  .header-actions { display: flex; align-items: center; gap: 0.6rem; }
  .btn {
    display: inline-flex; align-items: center; gap: 0.45rem;
    padding: 0.5rem 1rem;
    border-radius: 10px;
    font-size: 0.82rem; font-weight: 600; font-family: var(--font);
    cursor: pointer; border: none;
    transition: all 0.2s;
  }
  .btn-primary { background: var(--accent); color: #fff; }
  .btn-primary:hover { opacity: 0.88; box-shadow: 0 4px 14px rgba(67,24,255,0.3); }
  .btn-ghost {
    background: var(--card); color: var(--text-sec);
    border: 1px solid var(--border);
  }
  .btn-ghost:hover { background: var(--bg); }
  .btn svg { width: 16px; height: 16px; }
  .hamburger {
    display: none; background: none; border: none;
    color: var(--text); cursor: pointer; padding: 0.3rem;
  }
  .hamburger svg { width: 24px; height: 24px; }

  /* ── Content ──────────────────────────────────────────────────────── */
  main {
    flex: 1;
    max-width: 1440px;
    width: 100%;
    margin: 0 auto;
    padding: 2rem 2.25rem;
  }

  /* ── Demo banner ──────────────────────────────────────────────────── */
  .demo-banner {
    background: var(--orange-bg); border: 1px solid rgba(255,181,71,0.25);
    border-radius: var(--radius-sm);
    padding: 0.7rem 1.15rem;
    font-size: 0.82rem; color: var(--text-sec);
    margin-bottom: 1.75rem;
    display: flex; align-items: center; gap: 0.5rem;
  }
  .demo-banner strong { color: var(--orange); }

  /* ── KPI Cards ────────────────────────────────────────────────────── */
  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1.5rem;
    margin-bottom: 2rem;
  }
  .kpi {
    background: var(--card);
    border-radius: var(--radius);
    padding: 1.35rem 1.5rem;
    box-shadow: var(--shadow-sm);
    display: flex; flex-direction: column; gap: 0.5rem;
    border-left: 4px solid transparent;
    transition: box-shadow 0.2s, transform 0.2s;
  }
  .kpi:hover { box-shadow: var(--shadow-md); transform: translateY(-2px); }
  .kpi.kpi-accent  { border-left-color: var(--accent); }
  .kpi.kpi-green   { border-left-color: var(--green); }
  .kpi.kpi-orange  { border-left-color: var(--orange); }
  .kpi.kpi-red     { border-left-color: var(--red); }
  .kpi-top { display: flex; align-items: flex-start; justify-content: space-between; }
  .kpi-icon {
    width: 44px; height: 44px;
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }
  .kpi-icon svg { width: 20px; height: 20px; }
  .kpi-icon.accent { background: var(--accent-bg); color: var(--accent); }
  .kpi-icon.green  { background: var(--green-bg);  color: var(--green);  }
  .kpi-icon.red    { background: var(--red-bg);    color: var(--red);    }
  .kpi-icon.orange { background: var(--orange-bg); color: var(--orange); }
  .kpi-label {
    font-size: 0.75rem; font-weight: 600; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .kpi-value {
    font-size: 2.1rem; font-weight: 800; line-height: 1.1;
    letter-spacing: -0.03em; color: var(--text);
    font-variant-numeric: tabular-nums;
  }
  .kpi-footer { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.15rem; }
  .kpi-trend {
    display: inline-flex; align-items: center; gap: 0.2rem;
    font-size: 0.75rem; font-weight: 700;
    padding: 2px 8px; border-radius: 6px;
  }
  .kpi-trend.up   { background: var(--green-bg); color: var(--green); }
  .kpi-trend.down { background: var(--red-bg);   color: var(--red);   }
  .kpi-trend.flat { background: var(--accent-bg); color: var(--muted); }
  .kpi-trend svg  { width: 12px; height: 12px; }
  .kpi-sub { font-size: 0.72rem; color: var(--muted); font-weight: 500; }

  /* ── Section headers ──────────────────────────────────────────────── */
  .section-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 1.25rem;
  }
  .section-header h2 {
    font-size: 1.1rem; font-weight: 700; color: var(--text);
    letter-spacing: -0.01em;
  }
  .section-badge {
    font-size: 0.72rem; font-weight: 600; color: var(--muted);
    background: var(--bg); padding: 4px 12px;
    border-radius: 8px; border: 1px solid var(--border);
  }

  /* ── Insights section ─────────────────────────────────────────────── */
  .insights-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.5rem;
    margin-bottom: 2.25rem;
  }
  .insight-card {
    background: var(--card);
    border-radius: var(--radius);
    box-shadow: var(--shadow-sm);
    overflow: hidden;
  }
  .insight-card-header {
    padding: 1.1rem 1.5rem 0.75rem;
    display: flex; align-items: center; gap: 0.6rem;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.85rem;
  }
  .insight-card-header h3 {
    font-size: 0.9rem; font-weight: 700; color: var(--text);
  }
  .insight-card-header .ic-icon {
    width: 32px; height: 32px; border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }
  .insight-card-header .ic-icon svg { width: 16px; height: 16px; }
  .ic-icon.warn-bg { background: var(--orange-bg); color: var(--orange); }
  .ic-icon.ok-bg   { background: var(--green-bg); color: var(--green); }
  .ic-icon.info-bg  { background: var(--info-bg); color: var(--info); }
  .insight-list { padding: 0.5rem 0; }
  .insight-item {
    display: flex; align-items: center; gap: 0.85rem;
    padding: 0.75rem 1.5rem;
    transition: background 0.15s;
  }
  .insight-item:hover { background: var(--accent-bg); }
  .insight-dot {
    width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
  }
  .insight-item.danger .insight-dot { background: var(--red); }
  .insight-item.warn .insight-dot   { background: var(--orange); }
  .insight-item.ok .insight-dot     { background: var(--green); }
  .insight-item.info .insight-dot   { background: var(--info); }
  .insight-item.up .insight-dot     { background: var(--green); }
  .insight-item.down .insight-dot   { background: var(--red); }
  .insight-item.flat .insight-dot   { background: var(--muted); }
  .insight-content { flex: 1; min-width: 0; }
  .insight-title {
    font-size: 0.84rem; font-weight: 600; color: var(--text);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .insight-desc {
    font-size: 0.74rem; color: var(--muted); font-weight: 500;
    margin-top: 1px;
  }
  .insight-metric {
    font-size: 0.85rem; font-weight: 700;
    padding: 3px 10px; border-radius: 8px;
    white-space: nowrap; flex-shrink: 0;
  }
  .insight-item.danger .insight-metric { background: var(--red-bg); color: var(--red); }
  .insight-item.warn .insight-metric   { background: var(--orange-bg); color: var(--orange); }
  .insight-item.ok .insight-metric     { background: var(--green-bg); color: var(--green); }
  .insight-item.info .insight-metric   { background: var(--info-bg); color: var(--info); }

  /* ── Chart cards ──────────────────────────────────────────────────── */
  .chart-row {
    display: grid; gap: 1.5rem;
    margin-bottom: 1.5rem;
  }
  .chart-row.r-2-1  { grid-template-columns: 2fr 1fr; }
  .chart-row.r-1-1  { grid-template-columns: 1fr 1fr; }
  .chart-row.r-1-1-1 { grid-template-columns: 1fr 1fr 1fr; }
  .chart-row.r-full { grid-template-columns: 1fr; }
  .card {
    background: var(--card);
    border-radius: var(--radius);
    padding: 1.4rem 1.5rem;
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.2s;
  }
  .card:hover { box-shadow: var(--shadow-md); }

  /* ── Table ────────────────────────────────────────────────────────── */
  .table-wrap { overflow-x: auto; border-radius: 12px; }
  table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 0.82rem; }
  thead th {
    background: var(--bg);
    color: var(--muted); font-weight: 700;
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
    padding: 0.7rem 1rem; text-align: left;
    border-bottom: 1px solid var(--border);
    position: sticky; top: 0;
  }
  thead th:first-child { border-radius: 10px 0 0 0; }
  thead th:last-child  { border-radius: 0 10px 0 0; }
  td {
    padding: 0.65rem 1rem;
    border-bottom: 1px solid var(--border);
    color: var(--text-sec);
    font-family: var(--font-body);
  }
  tr:last-child td { border-bottom: none; }
  tbody tr { transition: background 0.15s; }
  tbody tr:hover td { background: var(--accent-bg); }
  .mono { font-family: var(--font-mono); font-size: 0.75rem; letter-spacing: -0.01em; }
  .pill {
    display: inline-flex; align-items: center;
    padding: 3px 10px; border-radius: 8px;
    font-size: 0.72rem; font-weight: 700;
  }
  .pill-green  { background: var(--green-bg); color: var(--green); }
  .pill-red    { background: var(--red-bg);   color: var(--red);   }
  .pill-blue   { background: var(--accent-bg); color: var(--accent); }
  .pill-orange { background: var(--orange-bg); color: var(--orange); }
  .pill-teal   { background: rgba(13,202,240,0.1); color: #0DCAF0; }
  .btn-action {
    border: none; border-radius: 6px; padding: 4px 10px; font-size: .72rem;
    font-weight: 600; cursor: pointer; font-family: var(--font-body);
    transition: opacity .15s;
  }
  .btn-action:hover { opacity: .8; }
  .btn-action:disabled { opacity: .4; cursor: not-allowed; }
  .btn-blue  { background: var(--accent-bg); color: var(--accent); }
  .btn-green { background: var(--green-bg);  color: var(--green); }
  .btn-red   { background: var(--red-bg);    color: var(--red); }
  .pii-note {
    font-size: 0.72rem; color: var(--muted); margin-bottom: 0.75rem;
    font-weight: 500; font-family: var(--font-body);
  }

  /* ── Audit ────────────────────────────────────────────────────────── */
  details summary {
    cursor: pointer; font-size: 0.85rem; font-weight: 700;
    color: var(--text-sec); padding: 0.5rem 0; user-select: none;
    display: flex; align-items: center; gap: 0.5rem;
    list-style: none;
  }
  details summary::-webkit-details-marker { display: none; }
  details summary::before {
    content: '';
    display: inline-block; width: 0; height: 0;
    border-left: 5px solid var(--muted);
    border-top: 4px solid transparent;
    border-bottom: 4px solid transparent;
    transition: transform 0.2s;
  }
  details[open] summary::before { transform: rotate(90deg); }
  details[open] summary { margin-bottom: 0.75rem; }

  /* ── Footer ───────────────────────────────────────────────────────── */
  footer.page-footer {
    text-align: center; color: var(--muted);
    font-size: 0.72rem; padding: 1.5rem 2rem 2.5rem;
    font-family: var(--font-body);
  }

  /* ── Responsive ───────────────────────────────────────────────────── */
  @media (max-width: 1024px) {
    .kpi-grid { grid-template-columns: repeat(2, 1fr); }
    .chart-row.r-2-1, .chart-row.r-1-1, .chart-row.r-1-1-1 {
      grid-template-columns: 1fr;
    }
    .insights-grid { grid-template-columns: 1fr; }
  }
  @media (max-width: 768px) {
    .sidebar { transform: translateX(-100%); }
    .sidebar.open { transform: translateX(0); box-shadow: var(--shadow-lg); }
    .main-wrap { margin-left: 0; }
    .hamburger { display: block; }
    main { padding: 1.25rem; }
    .top-header { padding: 0.8rem 1rem; }
    .kpi-grid { grid-template-columns: 1fr 1fr; gap: 0.75rem; }
    .kpi-value { font-size: 1.5rem; }
  }
  @media (max-width: 480px) {
    .kpi-grid { grid-template-columns: 1fr; }
  }

  .sidebar-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.4); z-index: 99;
  }
  .sidebar-overlay.show { display: block; }

  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--muted); border-radius: 3px; opacity: 0.5; }

  html { scroll-behavior: smooth; }
  @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }

  .plotly .main-svg text { fill: var(--text-sec) !important; }
</style>
</head>
<body>

<div class="sidebar-overlay" id="sidebarOverlay"></div>

<aside class="sidebar" id="sidebar">
  <div class="sidebar-brand">
    <div class="logo">CB</div>
    <div>
      <div class="brand-text">CreditBot</div>
      <div class="brand-sub">Analytics Dashboard</div>
    </div>
  </div>
  <div class="sidebar-divider"></div>
  <nav class="sidebar-nav">
    <a href="#overview" class="active" data-section="overview">
      <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>
      Overview
    </a>
    <a href="#insights" data-section="insights">
      <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
      Insights
    </a>
    <a href="#analytics" data-section="analytics">
      <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M3 3v18h18"/><path d="M7 16l4-4 4 4 5-6"/></svg>
      Analytics
    </a>
    <a href="#applications" data-section="applications">
      <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
      Applications
    </a>
    <a href="#leads" data-section="leads">
      <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4-4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/></svg>
      Leads
    </a>
    <a href="#audit" data-section="audit">
      <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
      Audit Log
    </a>
  </nav>
  <div class="sidebar-footer">
    <p>Readiness metrics only.<br>No lending decisions are recorded.</p>
  </div>
</aside>

<div class="main-wrap">
  <header class="top-header">
    <div style="display:flex;align-items:center;gap:0.75rem">
      <button class="hamburger" id="menuBtn" aria-label="Toggle menu">
        <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
      </button>
      <div>
        <h1>Dashboard</h1>
        <div class="subtitle">Application readiness overview &middot; Auto-refreshes every 60s</div>
      </div>
    </div>
    <div class="header-actions">
      <button class="btn btn-ghost" id="themeBtn" title="Toggle theme">
        <svg id="themeIcon" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
      </button>
      <button class="btn btn-primary" onclick="location.reload()">
        <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
        Refresh
      </button>
    </div>
  </header>

  <main>

    {% if is_demo %}
    <div class="demo-banner">
      <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      <span><strong>Demo mode</strong> &mdash; Supabase not configured. Showing 60 synthetic sample applications.</span>
    </div>
    {% endif %}

    <!-- KPI cards -->
    <section id="overview">
    <div class="kpi-grid">
      <div class="kpi kpi-accent">
        <div class="kpi-top">
          <div>
            <div class="kpi-label">Total applications</div>
            <div class="kpi-value">{{ total }}</div>
          </div>
          <div class="kpi-icon accent">
            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/></svg>
          </div>
        </div>
        <div class="kpi-footer">
          <span class="kpi-trend {{ vol_trend_dir }}">
            {% if vol_trend_dir == 'up' %}<svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 2l4 5H2z"/></svg>{% elif vol_trend_dir == 'down' %}<svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 10L2 5h8z"/></svg>{% else %}&mdash;{% endif %}
            {{ vol_trend }}%
          </span>
          <span class="kpi-sub">{{ proc_count }} processed</span>
        </div>
      </div>

      <div class="kpi kpi-green">
        <div class="kpi-top">
          <div>
            <div class="kpi-label">Avg completeness</div>
            <div class="kpi-value">{{ avg_comp }}%</div>
          </div>
          <div class="kpi-icon green">
            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
          </div>
        </div>
        <div class="kpi-footer">
          <span class="kpi-trend {{ comp_trend_dir }}">
            {% if comp_trend_dir == 'up' %}<svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 2l4 5H2z"/></svg>{% elif comp_trend_dir == 'down' %}<svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 10L2 5h8z"/></svg>{% else %}&mdash;{% endif %}
            {{ comp_trend }}%
          </span>
          <span class="kpi-sub">across processed apps</span>
        </div>
      </div>

      <div class="kpi kpi-orange">
        <div class="kpi-top">
          <div>
            <div class="kpi-label">Ready for review</div>
            <div class="kpi-value">{{ ready_rate }}%</div>
          </div>
          <div class="kpi-icon orange">
            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          </div>
        </div>
        <div class="kpi-footer">
          <span class="kpi-trend {{ ready_trend_dir }}">
            {% if ready_trend_dir == 'up' %}<svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 2l4 5H2z"/></svg>{% elif ready_trend_dir == 'down' %}<svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 10L2 5h8z"/></svg>{% else %}&mdash;{% endif %}
            {{ ready_trend }}%
          </span>
          <span class="kpi-sub">{{ ready_count }} of {{ proc_count }}</span>
        </div>
      </div>

      <div class="kpi kpi-red">
        <div class="kpi-top">
          <div>
            <div class="kpi-label">Avg turnaround</div>
            <div class="kpi-value">{{ avg_turn }}s</div>
          </div>
          <div class="kpi-icon red">
            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
          </div>
        </div>
        <div class="kpi-footer">
          <span class="kpi-trend {{ turn_trend_dir }}">
            {% if turn_trend_dir == 'up' %}<svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 10L2 5h8z"/></svg>{% elif turn_trend_dir == 'down' %}<svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 2l4 5H2z"/></svg>{% else %}&mdash;{% endif %}
            {{ turn_trend }}%
          </span>
          <span class="kpi-sub">Claude extraction + validation</span>
        </div>
      </div>
    </div>
    </section>

    <!-- Insights & Alerts -->
    <section id="insights">
    <div class="section-header">
      <h2>Insights &amp; Alerts</h2>
      <span class="section-badge">Auto-generated</span>
    </div>
    <div class="insights-grid">
      <div class="insight-card">
        <div class="insight-card-header">
          <div class="ic-icon warn-bg">
            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          </div>
          <h3>What Needs Attention</h3>
        </div>
        <div class="insight-list">
          {% for item in attention_items %}
          <div class="insight-item {{ item.type }}">
            <div class="insight-dot"></div>
            <div class="insight-content">
              <div class="insight-title">{{ item.title }}</div>
              <div class="insight-desc">{{ item.desc }}</div>
            </div>
            {% if item.metric %}<div class="insight-metric">{{ item.metric }}</div>{% endif %}
          </div>
          {% endfor %}
        </div>
      </div>

      <div class="insight-card">
        <div class="insight-card-header">
          <div class="ic-icon info-bg">
            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M3 3v18h18"/><path d="M7 16l4-4 4 4 5-6"/></svg>
          </div>
          <h3>Performance Summary</h3>
        </div>
        <div class="insight-list">
          {% for item in quick_insights %}
          <div class="insight-item {{ item.type }}">
            <div class="insight-dot"></div>
            <div class="insight-content">
              <div class="insight-title">{{ item.title }}</div>
              <div class="insight-desc">{{ item.desc }}</div>
            </div>
          </div>
          {% endfor %}
        </div>
      </div>
    </div>
    </section>

    <!-- Analytics -->
    <section id="analytics">
    <div class="section-header">
      <h2>Analytics</h2>
      <span class="section-badge">Last 30 days</span>
    </div>

    <div class="chart-row r-2-1">
      <div class="card"><div id="chart-volume"></div></div>
      <div class="card"><div id="chart-ready"></div></div>
    </div>

    <div class="chart-row r-1-1">
      <div class="card"><div id="chart-flags"></div></div>
      <div class="card"><div id="chart-channels"></div></div>
    </div>

    <div class="chart-row r-1-1-1">
      <div class="card"><div id="chart-completeness"></div></div>
      <div class="card"><div id="chart-gender"></div></div>
      <div class="card"><div id="chart-turnaround"></div></div>
    </div>

    <div class="section-header" style="margin-top:0.5rem">
      <h2>Product Analytics</h2>
      <span class="section-badge">By loan product</span>
    </div>

    <div class="chart-row r-1-1-1">
      <div class="card"><div id="chart-products"></div></div>
      <div class="card"><div id="chart-product-readiness"></div></div>
      <div class="card"><div id="chart-institution"></div></div>
    </div>
    </section>

    <!-- Applications table -->
    <section id="applications" style="margin-top:0.5rem">
    <div class="section-header">
      <h2>Recent Applications</h2>
      <span class="section-badge">Last 30</span>
    </div>
    <div class="card" style="padding:0;overflow:hidden">
      <div style="padding:1.1rem 1.5rem 0">
        <p class="pii-note">PII fields (name, phone, address) are not shown per data-handling guardrail.</p>
      </div>
      <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Reference</th><th>Product</th><th>Submitted</th><th>Complete</th>
            <th>Ready</th><th>Officer</th><th>Notified via</th><th>Turnaround</th><th>Docs</th>
          </tr>
        </thead>
        <tbody>
        {% for row in table_rows %}
        <tr>
          <td class="mono">{{ row.ref }}</td>
          <td>
            {% if row.product %}
              <span class="pill pill-blue">{{ row.product }}</span>
            {% else %}&mdash;{% endif %}
          </td>
          <td>{{ row.submitted }}</td>
          <td>{{ row.completeness }}</td>
          <td>
            {% if row.ready == "Yes" %}
              <span class="pill pill-green">Ready</span>
            {% else %}
              <span class="pill pill-red">Not ready</span>
            {% endif %}
          </td>
          <td>{% if row.officer %}{{ row.officer }}{% else %}&mdash;{% endif %}</td>
          <td>
            {% if row.channel == "telegram" %}
              <span class="pill pill-blue">Telegram</span>
            {% elif row.channel == "whatsapp" %}
              <span class="pill pill-green">WhatsApp</span>
            {% elif row.channel == "sms" %}
              <span class="pill pill-orange">SMS</span>
            {% elif row.channel == "email" %}
              <span class="pill pill-teal">Email</span>
            {% elif row.channel %}
              <span class="pill pill-blue">{{ row.channel }}</span>
            {% else %}&mdash;{% endif %}
          </td>
          <td class="mono">{{ row.turnaround }}</td>
          <td>
            {% if row.ref %}
              <a href="/documents/{{ row.ref }}" target="_blank" style="color:var(--accent);text-decoration:none;font-weight:600;font-size:.8rem">View</a>
            {% else %}&mdash;{% endif %}
          </td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
      </div>
    </div>
    </section>

    <!-- Leads -->
    <section id="leads" style="margin-top:1.75rem">
    <div class="section-header">
      <h2>Leads</h2>
      <span class="section-badge">{{ lead_rows|length }} abandoned</span>
    </div>
    <div class="card" style="padding:0;overflow:hidden">
      <div style="padding:1.1rem 1.5rem 0">
        <p class="pii-note">Applicants who started but did not complete their application. Follow up to convert.</p>
      </div>
      {% if lead_rows %}
      <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th><th>Phone</th><th>Email</th><th>Product</th>
            <th>Stage reached</th><th>Source</th><th>Status</th><th>Date</th><th>Action</th>
          </tr>
        </thead>
        <tbody>
        {% for lead in lead_rows %}
        <tr id="lead-row-{{ lead.id }}">
          <td>{{ lead.name if lead.name else '&mdash;' }}</td>
          <td class="mono">{{ lead.phone if lead.phone else '&mdash;' }}</td>
          <td>{{ lead.email if lead.email else '&mdash;' }}</td>
          <td>
            {% if lead.product %}
              <span class="pill pill-blue">{{ lead.product }}</span>
            {% else %}&mdash;{% endif %}
          </td>
          <td><span class="pill pill-orange">{{ lead.stage }}</span></td>
          <td>
            {% if lead.source == 'cancelled' %}
              <span class="pill pill-red">Cancelled</span>
            {% elif lead.source == 'timeout' %}
              <span class="pill pill-orange">Timeout</span>
            {% else %}
              <span class="pill pill-blue">{{ lead.source }}</span>
            {% endif %}
          </td>
          <td id="lead-status-{{ lead.id }}">
            {% if lead.status == 'new' %}
              <span class="pill pill-orange">New</span>
            {% elif lead.status == 'contacted' %}
              <span class="pill pill-blue">Contacted</span>
            {% elif lead.status == 'converted' %}
              <span class="pill pill-green">Converted</span>
            {% elif lead.status == 'closed' %}
              <span class="pill pill-red">Closed</span>
            {% else %}
              <span class="pill pill-blue">{{ lead.status }}</span>
            {% endif %}
          </td>
          <td>{{ lead.date }}</td>
          <td id="lead-actions-{{ lead.id }}">
            {% if lead.status == 'new' %}
              <button class="btn-action btn-blue" onclick="updateLead('{{ lead.id }}','contacted')">Contacted</button>
              <button class="btn-action btn-red" onclick="updateLead('{{ lead.id }}','closed')">Close</button>
            {% elif lead.status == 'contacted' %}
              <button class="btn-action btn-green" onclick="updateLead('{{ lead.id }}','converted')">Converted</button>
              <button class="btn-action btn-red" onclick="updateLead('{{ lead.id }}','closed')">Close</button>
            {% elif lead.status == 'converted' %}
              <span style="color:var(--muted);font-size:.78rem">Done</span>
            {% elif lead.status == 'closed' %}
              <span style="color:var(--muted);font-size:.78rem">Closed</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
      </div>
      {% else %}
      <div style="padding:1rem 1.5rem;color:var(--muted);font-size:.82rem">
        No leads captured yet. Leads appear when applicants cancel or time out after providing personal details.
      </div>
      {% endif %}
    </div>
    </section>

    <!-- Audit log -->
    <section id="audit" style="margin-top:1.75rem">
    <div class="card">
      <details>
        <summary>Audit log (last 50 events)</summary>
        {% if audit_rows %}
        <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Timestamp</th><th>Event</th><th>Actor</th><th>Payload</th></tr>
          </thead>
          <tbody>
          {% for ev in audit_rows %}
          <tr>
            <td class="mono" style="white-space:nowrap">{{ ev.ts }}</td>
            <td><span class="pill pill-blue">{{ ev.event_type }}</span></td>
            <td>{{ ev.actor }}</td>
            <td class="mono" style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              {{ ev.payload }}</td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
        </div>
        {% else %}
        <p style="color:var(--muted);font-size:.82rem;padding:.5rem 0">
          No audit events recorded yet (or Supabase not connected).
        </p>
        {% endif %}
      </details>
    </div>
    </section>

  </main>

  <footer class="page-footer">
    CreditBot Analytics &middot; This dashboard shows readiness metrics only &mdash; no lending decisions are recorded here.
  </footer>
</div>

<script>
const cfg = {responsive: true, displayModeBar: false};
const isDark = () => document.documentElement.dataset.theme === 'dark' ||
  (!document.documentElement.dataset.theme &&
   window.matchMedia('(prefers-color-scheme: dark)').matches);

function themeLayout(layout) {
  const c = isDark() ? '#A3AED0' : '#2B3674';
  const g = isDark() ? 'rgba(163,174,208,0.08)' : 'rgba(163,174,208,0.15)';
  layout.font = Object.assign(layout.font || {}, {color: c});
  if (layout.xaxis) layout.xaxis.gridcolor = g;
  if (layout.yaxis) layout.yaxis.gridcolor = g;
  return layout;
}

function renderChart(id, spec) {
  if (!spec || !spec.data) return;
  spec.layout = themeLayout(spec.layout || {});
  Plotly.newPlot(id, spec.data, spec.layout, cfg);
}

const charts = {
  'chart-volume':            {{ volume_json            | safe }},
  'chart-ready':             {{ ready_json             | safe }},
  'chart-flags':             {{ flags_json             | safe }},
  'chart-channels':          {{ channels_json          | safe }},
  'chart-completeness':      {{ completeness_json      | safe }},
  'chart-gender':            {{ gender_json            | safe }},
  'chart-turnaround':        {{ turnaround_json        | safe }},
  'chart-products':          {{ products_json          | safe }},
  'chart-product-readiness': {{ product_readiness_json | safe }},
  'chart-institution':       {{ institution_json       | safe }}
};
Object.entries(charts).forEach(([id, spec]) => renderChart(id, spec));

const themeBtn = document.getElementById('themeBtn');
const themeIcon = document.getElementById('themeIcon');
const sunPath = '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
const moonPath = '<path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>';

function applyTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  themeIcon.innerHTML = dark ? moonPath : sunPath;
  try { localStorage.setItem('cb-theme', dark ? 'dark' : 'light'); } catch(e) {}
  Object.entries(charts).forEach(([id, spec]) => renderChart(id, spec));
}
try {
  const saved = localStorage.getItem('cb-theme');
  if (saved) applyTheme(saved === 'dark');
} catch(e) {}
themeBtn.addEventListener('click', () => applyTheme(!isDark()));

const sidebar = document.getElementById('sidebar');
const overlay = document.getElementById('sidebarOverlay');
const menuBtn = document.getElementById('menuBtn');
menuBtn.addEventListener('click', () => {
  sidebar.classList.toggle('open');
  overlay.classList.toggle('show');
});
overlay.addEventListener('click', () => {
  sidebar.classList.remove('open');
  overlay.classList.remove('show');
});

const navLinks = document.querySelectorAll('.sidebar-nav a[data-section]');
const sections = {};
navLinks.forEach(a => {
  const id = a.dataset.section;
  const el = document.getElementById(id);
  if (el) sections[id] = {el, link: a};
});
const observer = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      navLinks.forEach(a => a.classList.remove('active'));
      const match = Object.values(sections).find(s => s.el === e.target);
      if (match) match.link.classList.add('active');
    }
  });
}, {rootMargin: '-80px 0px -60% 0px'});
Object.values(sections).forEach(s => observer.observe(s.el));

navLinks.forEach(a => a.addEventListener('click', () => {
  sidebar.classList.remove('open');
  overlay.classList.remove('show');
}));

setTimeout(() => location.reload(), 60000);

function updateLead(leadId, newStatus) {
  var btns = document.querySelectorAll('#lead-actions-' + leadId + ' button');
  btns.forEach(function(b) { b.disabled = true; b.textContent = '...'; });
  fetch('/api/leads/' + leadId + '/status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: newStatus})
  }).then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.ok) {
        var statusMap = {
          contacted: '<span class="pill pill-blue">Contacted</span>',
          converted: '<span class="pill pill-green">Converted</span>',
          closed: '<span class="pill pill-red">Closed</span>'
        };
        var actionsMap = {
          contacted: '<button class="btn-action btn-green" onclick="updateLead(\'' + leadId + '\',\'converted\')">Converted</button> <button class="btn-action btn-red" onclick="updateLead(\'' + leadId + '\',\'closed\')">Close</button>',
          converted: '<span style="color:var(--muted);font-size:.78rem">Done</span>',
          closed: '<span style="color:var(--muted);font-size:.78rem">Closed</span>'
        };
        document.getElementById('lead-status-' + leadId).innerHTML = statusMap[newStatus] || newStatus;
        document.getElementById('lead-actions-' + leadId).innerHTML = actionsMap[newStatus] || '';
      } else {
        alert('Failed to update: ' + (data.error || 'unknown error'));
        btns.forEach(function(b) { b.disabled = false; });
      }
    }).catch(function() {
      alert('Network error — try again');
      btns.forEach(function(b) { b.disabled = false; });
    });
}
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    apps_df, audit_df, is_demo = load_data()

    if not apps_df.empty:
        apps_df["created_at"] = pd.to_datetime(apps_df["created_at"], utc=True, errors="coerce")
        apps_df["date"] = apps_df["created_at"].dt.date
        apps_df["completeness_pct"] = pd.to_numeric(apps_df["completeness_pct"], errors="coerce").fillna(0)
        apps_df["turnaround_seconds"] = pd.to_numeric(apps_df["turnaround_seconds"], errors="coerce").fillna(0)
        apps_df["ready_for_underwriting"] = apps_df["ready_for_underwriting"].fillna(False).astype(bool)

    processed = apps_df[apps_df["status"] == "processed"] if not apps_df.empty else apps_df

    total       = len(apps_df)
    proc_count  = len(processed)
    avg_comp    = round(processed["completeness_pct"].mean(), 1) if proc_count else 0
    ready_count = int(processed["ready_for_underwriting"].sum()) if proc_count else 0
    ready_rate  = round(ready_count / proc_count * 100, 1) if proc_count else 0
    avg_turn    = round(processed["turnaround_seconds"].mean(), 1) if proc_count else 0

    now_utc = pd.Timestamp.now(tz="UTC")
    mid = now_utc - pd.Timedelta(days=15)
    recent_apps = apps_df[apps_df["created_at"] >= mid] if not apps_df.empty else apps_df
    prev_apps   = apps_df[(apps_df["created_at"] < mid)] if not apps_df.empty else apps_df
    recent_proc = processed[processed["created_at"] >= mid] if proc_count else processed
    prev_proc   = processed[processed["created_at"] < mid] if proc_count else processed

    def _trend(recent_val, prev_val):
        if prev_val == 0:
            return (0.0, "flat") if recent_val == 0 else (100.0, "up")
        pct = round((recent_val - prev_val) / abs(prev_val) * 100, 1)
        pct = max(-999, min(999, pct))
        return (abs(pct), "up" if pct > 0 else ("down" if pct < 0 else "flat"))

    vol_trend, vol_trend_dir = _trend(len(recent_apps), len(prev_apps))
    r_comp = recent_proc["completeness_pct"].mean() if len(recent_proc) else 0
    p_comp = prev_proc["completeness_pct"].mean() if len(prev_proc) else 0
    comp_trend, comp_trend_dir = _trend(r_comp, p_comp)
    r_ready = (recent_proc["ready_for_underwriting"].sum() / max(len(recent_proc), 1) * 100) if len(recent_proc) else 0
    p_ready = (prev_proc["ready_for_underwriting"].sum() / max(len(prev_proc), 1) * 100) if len(prev_proc) else 0
    ready_trend, ready_trend_dir = _trend(r_ready, p_ready)
    r_turn = recent_proc["turnaround_seconds"].mean() if len(recent_proc) else 0
    p_turn = prev_proc["turnaround_seconds"].mean() if len(prev_proc) else 0
    turn_trend, turn_trend_dir = _trend(r_turn, p_turn)
    if turn_trend_dir == "up":
        turn_trend_dir = "down"
    elif turn_trend_dir == "down":
        turn_trend_dir = "up"

    # Attention metrics
    flagged_count = 0
    blocker_count = 0
    if proc_count and "flags" in processed.columns:
        for flags_val in processed["flags"].dropna():
            items = flags_val if isinstance(flags_val, list) else []
            if items:
                flagged_count += 1
                if any((f.get("severity") == "blocker" if isinstance(f, dict) else False) for f in items):
                    blocker_count += 1
    below_threshold = int((processed["completeness_pct"] < 75).sum()) if proc_count else 0
    notified = processed[processed["notification_channel"].notna() & (processed["notification_channel"] != "none")] if proc_count else processed
    delivered = processed[processed.get("notification_status", pd.Series()) == "delivered"] if proc_count and "notification_status" in processed.columns else pd.DataFrame()
    delivery_rate = round(len(delivered) / max(len(notified), 1) * 100, 1) if proc_count else 0

    # ── Insights computation ─────────────────────────────────────────
    attention_items = []
    quick_insights = []

    if flagged_count > 0:
        attention_items.append({
            "type": "danger" if blocker_count > 0 else "warn",
            "title": f"{flagged_count} applications flagged",
            "desc": f"{blocker_count} with blocker-level flags requiring review" if blocker_count else "Review flags for potential document issues",
            "metric": str(flagged_count),
        })

    if below_threshold > 0:
        attention_items.append({
            "type": "warn",
            "title": f"{below_threshold} below 75% completeness",
            "desc": "Documents may be missing or unreadable",
            "metric": str(below_threshold),
        })

    if delivery_rate < 100 and proc_count > 0:
        attention_items.append({
            "type": "warn" if delivery_rate < 90 else "info",
            "title": f"Delivery rate at {delivery_rate}%",
            "desc": "Some officer notifications failed to deliver",
            "metric": f"{delivery_rate}%",
        })

    if ready_trend_dir == "down" and ready_trend > 5:
        attention_items.append({
            "type": "warn",
            "title": f"Readiness down {ready_trend}%",
            "desc": "Fewer applications passing readiness checks vs prior period",
            "metric": f"-{ready_trend}%",
        })

    if not attention_items:
        attention_items.append({
            "type": "ok",
            "title": "All clear",
            "desc": "No issues requiring immediate attention",
            "metric": "",
        })

    quick_insights.append({
        "type": "up" if vol_trend_dir == "up" else ("down" if vol_trend_dir == "down" else "flat"),
        "title": f"Volume {'up' if vol_trend_dir == 'up' else ('down' if vol_trend_dir == 'down' else 'steady')} {vol_trend}% vs prior period",
        "desc": f"{total} total applications in the last 30 days",
    })

    if proc_count > 0:
        quick_insights.append({
            "type": "up" if avg_comp >= 75 else "down",
            "title": f"Avg completeness at {avg_comp}%",
            "desc": f"{'Above' if avg_comp >= 75 else 'Below'} the 75% readiness threshold",
        })
        quick_insights.append({
            "type": "up" if ready_rate >= 50 else "down",
            "title": f"{ready_rate}% ready for underwriting",
            "desc": f"{ready_count} of {proc_count} processed applications qualify",
        })
        quick_insights.append({
            "type": "up" if avg_turn < 30 else ("flat" if avg_turn < 120 else "down"),
            "title": f"Avg processing time: {avg_turn}s",
            "desc": "Claude extraction + validation pipeline speed",
        })

    # Product insights
    has_products = proc_count and "product_name" in processed.columns and processed["product_name"].notna().any()
    if has_products:
        product_counts = processed["product_name"].value_counts()
        top_product = product_counts.index[0] if len(product_counts) else None
        top_product_count = int(product_counts.iloc[0]) if len(product_counts) else 0
        num_products = len(product_counts)
        quick_insights.append({
            "type": "info",
            "title": f"{num_products} loan products active",
            "desc": f"Most popular: {top_product} ({top_product_count} applications)" if top_product else "Product data available",
        })

    # Charts
    volume_json       = chart_volume(apps_df) if not apps_df.empty else "{}"
    ready_json        = chart_ready(processed) if proc_count else "{}"
    flags_json        = chart_flags(processed) if proc_count else "{}"
    channels_json     = chart_channels(processed) if proc_count else "{}"
    completeness_json = chart_completeness(processed) if proc_count else "{}"
    gender_json       = chart_gender(processed) if proc_count else "{}"
    turnaround_json   = chart_turnaround(apps_df) if not apps_df.empty else "{}"
    products_json          = chart_products(processed) if proc_count else "{}"
    product_readiness_json = chart_product_readiness(processed) if proc_count else "{}"
    institution_json       = chart_institution_type(processed) if proc_count else "{}"

    def _extract_chart(j):
        obj = json.loads(j)
        return json.dumps({"data": obj.get("data", []), "layout": obj.get("layout", {})})

    # Recent applications table rows
    table_rows = []
    for _, r in apps_df.head(30).iterrows():
        ts = r["created_at"]
        product_label = r.get("product_name", "") if pd.notna(r.get("product_name")) else ""
        table_rows.append({
            "ref":         r.get("reference_number", ""),
            "product":     product_label,
            "submitted":   ts.strftime("%Y-%m-%d %H:%M") if pd.notna(ts) else "--",
            "completeness": f"{r['completeness_pct']:.1f}%",
            "ready":       "Yes" if pd.notna(r.get("ready_for_underwriting")) and r["ready_for_underwriting"] else "No",
            "officer":     r.get("officer_code", "") if pd.notna(r.get("officer_code")) else "",
            "channel":     r.get("notification_channel", "") if pd.notna(r.get("notification_channel")) else "",
            "turnaround":  f"{r['turnaround_seconds']:.1f}s",
        })

    # Lead rows
    lead_rows = []
    try:
        from storage import list_leads as _list_leads
        raw_leads = _list_leads(limit=100) if not is_demo else []
        for ld in raw_leads:
            ts_raw = ld.get("created_at", "")
            try:
                ts = pd.to_datetime(ts_raw, utc=True).strftime("%Y-%m-%d %H:%M")
            except Exception:
                ts = str(ts_raw)[:16]
            lead_rows.append({
                "id":      ld.get("id") or "",
                "name":    ld.get("declared_name") or "",
                "phone":   ld.get("phone_number") or "",
                "email":   ld.get("email") or "",
                "product": ld.get("product_name") or "",
                "stage":   (ld.get("stage_reached") or "unknown").replace("_", " ").title(),
                "source":  ld.get("source") or "abandoned",
                "status":  ld.get("status") or "new",
                "date":    ts,
            })
    except Exception:
        pass

    new_leads = sum(1 for ld in lead_rows if ld["status"] == "new")
    if new_leads > 0:
        quick_insights.append({
            "type": "info",
            "title": f"{new_leads} new lead{'s' if new_leads != 1 else ''} to follow up",
            "desc": f"{len(lead_rows)} total leads captured from abandoned sessions",
        })

    # Audit rows
    audit_rows = []
    if not audit_df.empty:
        for _, ev in audit_df.head(50).iterrows():
            ts_raw = ev.get("created_at", "")
            try:
                ts = pd.to_datetime(ts_raw, utc=True).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts = str(ts_raw)
            payload = ev.get("payload")
            payload_str = json.dumps(payload, ensure_ascii=False)[:120] if payload else ""
            audit_rows.append({
                "ts":         ts,
                "event_type": ev.get("event_type", ""),
                "actor":      ev.get("actor", ""),
                "payload":    payload_str,
            })

    from jinja2 import Environment
    env = Environment(autoescape=True)
    template = env.from_string(HTML)
    html = template.render(
        is_demo=is_demo,
        total=f"{total:,}",
        proc_count=f"{proc_count:,}",
        avg_comp=avg_comp,
        ready_rate=ready_rate,
        ready_count=ready_count,
        avg_turn=avg_turn,
        vol_trend=vol_trend, vol_trend_dir=vol_trend_dir,
        comp_trend=comp_trend, comp_trend_dir=comp_trend_dir,
        ready_trend=ready_trend, ready_trend_dir=ready_trend_dir,
        turn_trend=turn_trend, turn_trend_dir=turn_trend_dir,
        flagged_count=flagged_count,
        blocker_count=blocker_count,
        below_threshold=below_threshold,
        delivery_rate=delivery_rate,
        attention_items=attention_items,
        quick_insights=quick_insights,
        table_rows=table_rows,
        lead_rows=lead_rows,
        audit_rows=audit_rows,
        volume_json=_extract_chart(volume_json) if proc_count or not apps_df.empty else "{}",
        ready_json=_extract_chart(ready_json) if proc_count else "{}",
        flags_json=_extract_chart(flags_json) if proc_count else "{}",
        channels_json=_extract_chart(channels_json) if proc_count else "{}",
        completeness_json=_extract_chart(completeness_json) if proc_count else "{}",
        gender_json=_extract_chart(gender_json) if proc_count else "{}",
        turnaround_json=_extract_chart(turnaround_json) if proc_count or not apps_df.empty else "{}",
        products_json=_extract_chart(products_json) if proc_count else "{}",
        product_readiness_json=_extract_chart(product_readiness_json) if proc_count else "{}",
        institution_json=_extract_chart(institution_json) if proc_count else "{}",
    )
    return Response(html, mimetype="text/html")


DOCS_HTML = """
<title>Documents — {{ reference }}</title>
<style>
  :root {
    --bg: #f4f7fe; --card-bg: #ffffff; --text: #2B3674;
    --muted: #A3AED0; --accent: #4318FF; --border: #e9ecf4;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #0b1437; --card-bg: #111c44; --text: #e2e8f0;
      --muted: #718096; --accent: #7B61FF; --border: #1e2d5e;
    }
  }
  * { box-sizing: border-box; margin: 0; }
  body { font-family: "Plus Jakarta Sans", system-ui, sans-serif;
         background: var(--bg); color: var(--text); padding: 2rem; }
  .header { margin-bottom: 2rem; }
  .header h1 { font-size: 1.4rem; font-weight: 700; }
  .header p { color: var(--muted); font-size: .85rem; margin-top: .3rem; }
  .back { color: var(--accent); text-decoration: none; font-size: .85rem;
          font-weight: 600; display: inline-block; margin-bottom: 1rem; }
  .back:hover { text-decoration: underline; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
          gap: 1.25rem; }
  .doc-card { background: var(--card-bg); border-radius: 14px;
              border: 1px solid var(--border); overflow: hidden; }
  .doc-card .label { padding: .75rem 1rem; font-weight: 600; font-size: .9rem;
                     border-bottom: 1px solid var(--border); }
  .doc-card img { width: 100%; height: auto; display: block; cursor: pointer; }
  .doc-card .meta { padding: .5rem 1rem; color: var(--muted); font-size: .75rem; }
  .empty { background: var(--card-bg); border-radius: 14px; padding: 2rem;
           text-align: center; color: var(--muted); border: 1px solid var(--border); }
</style>

<a href="/" class="back">&larr; Back to dashboard</a>
<div class="header">
  <h1>Uploaded Documents</h1>
  <p>Reference: {{ reference }} &middot; {{ docs|length }} document{{ 's' if docs|length != 1 else '' }} on file</p>
</div>

{% if docs %}
<div class="grid">
  {% for doc in docs %}
  <div class="doc-card">
    <div class="label">{{ doc.label }}</div>
    <a href="{{ doc.url }}" target="_blank">
      <img src="{{ doc.url }}" alt="{{ doc.label }}" loading="lazy">
    </a>
    <div class="meta">Click image to open full size &middot; Link expires in 1 hour</div>
  </div>
  {% endfor %}
</div>
{% else %}
<div class="empty">
  <p>No documents stored for this application.</p>
  <p style="margin-top:.5rem;font-size:.8rem">Documents are stored for applications submitted after the storage feature was enabled.</p>
</div>
{% endif %}
"""


@app.route("/documents/<reference>")
def view_documents(reference):
    """Officer-facing page showing uploaded documents for an application."""
    import re
    if not re.match(r"^CRB-\d{8}-[a-f0-9]{4}$", reference):
        return Response("Invalid reference format", status=400)

    docs = []
    try:
        from document_storage import get_document_urls
        docs = get_document_urls(reference)
    except Exception:
        logger.exception("Failed to load documents for %s", reference)

    from jinja2 import Environment
    env = Environment(autoescape=True)
    template = env.from_string(DOCS_HTML)
    html = template.render(reference=reference, docs=docs)
    return Response(html, mimetype="text/html")


@app.route("/api/leads/<lead_id>/status", methods=["POST"])
def api_update_lead_status(lead_id):
    """API endpoint for officers to update lead status from the dashboard."""
    import re
    if not re.match(r"^[a-f0-9\-]{36}$", lead_id):
        return Response(json.dumps({"ok": False, "error": "invalid id"}),
                        status=400, mimetype="application/json")
    try:
        from flask import request as flask_request
        data = flask_request.get_json(force=True)
        new_status = data.get("status", "")
        if new_status not in ("contacted", "converted", "closed"):
            return Response(json.dumps({"ok": False, "error": "invalid status"}),
                            status=400, mimetype="application/json")
        from storage import update_lead_status
        update_lead_status(lead_id, new_status)
        return Response(json.dumps({"ok": True, "status": new_status}),
                        mimetype="application/json")
    except Exception:
        logger.exception("Failed to update lead %s", lead_id)
        return Response(json.dumps({"ok": False, "error": "server error"}),
                        status=500, mimetype="application/json")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    import os
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 5050))
    print(f"Dashboard running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
