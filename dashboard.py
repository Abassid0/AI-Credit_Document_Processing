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

BLUE   = "#1A6BFF"
GREEN  = "#21C55D"
RED    = "#FF4B4B"
ORANGE = "#F5A623"
WA     = "#25D366"

PLOTLY_THEME = dict(
    template="plotly_white",
    margin=dict(l=10, r=10, t=30, b=10),
    font=dict(family="Inter, system-ui, sans-serif", size=12),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
)


def fig_to_json(fig) -> str:
    return pio.to_json(fig)


def chart_volume(df: pd.DataFrame) -> str:
    daily = df.groupby("date").size().reset_index(name="count").sort_values("date")
    fig = px.area(daily, x="date", y="count",
                  color_discrete_sequence=[BLUE],
                  labels={"date": "", "count": "Applications"},
                  title="Applications submitted per day")
    fig.update_traces(line_width=2, fillcolor="rgba(26,107,255,0.12)")
    fig.update_layout(**PLOTLY_THEME, height=240)
    return fig_to_json(fig)


def chart_ready(processed: pd.DataFrame) -> str:
    ready_yes = int(processed["ready_for_underwriting"].sum())
    ready_no  = len(processed) - ready_yes
    fig = go.Figure(go.Pie(
        labels=["Ready", "Not ready"],
        values=[ready_yes, ready_no],
        hole=0.58,
        marker_colors=[GREEN, RED],
        textinfo="percent+label",
        textfont_size=13,
    ))
    fig.update_layout(**PLOTLY_THEME, height=240, title="Readiness split",
                      showlegend=False)
    return fig_to_json(fig)


def chart_flags(processed: pd.DataFrame) -> str:
    flag_counts: dict[str, int] = {}
    for flags_val in processed["flags"].dropna():
        items = flags_val if isinstance(flags_val, list) else []
        for f in items:
            label = (f.get("type") or f.get("message") or "unknown")[:60] if isinstance(f, dict) else str(f)[:60]
            flag_counts[label] = flag_counts.get(label, 0) + 1
    if not flag_counts:
        fig = go.Figure()
        fig.add_annotation(text="No flags raised", x=0.5, y=0.5, showarrow=False, font_size=16)
        fig.update_layout(**PLOTLY_THEME, height=240, title="Common flags")
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
    fig.update_layout(**PLOTLY_THEME, height=240)
    return fig_to_json(fig)


def chart_channels(processed: pd.DataFrame) -> str:
    notified = processed[processed["notification_channel"].notna()]
    if notified.empty:
        fig = go.Figure()
        fig.add_annotation(text="No notifications sent", x=0.5, y=0.5, showarrow=False, font_size=16)
        fig.update_layout(**PLOTLY_THEME, height=240, title="Notification channels")
        return fig_to_json(fig)
    ch_counts = notified["notification_channel"].value_counts().reset_index()
    ch_counts.columns = ["channel", "count"]
    color_map = {
        "telegram": "#0088CC", "sms": ORANGE,
        "whatsapp": WA, "email": BLUE, "none": "#BBBBBB",
    }
    fig = px.bar(ch_counts, x="channel", y="count", color="channel",
                 color_discrete_map=color_map,
                 labels={"channel": "", "count": "Notifications sent"},
                 title="Notification channels")
    fig.update_layout(**PLOTLY_THEME, height=240, showlegend=False)
    return fig_to_json(fig)


def chart_completeness(processed: pd.DataFrame) -> str:
    fig = px.histogram(processed, x="completeness_pct", nbins=20,
                       color_discrete_sequence=[BLUE],
                       labels={"completeness_pct": "Completeness (%)", "count": ""},
                       title="Completeness distribution")
    fig.add_vline(x=75, line_dash="dash", line_color=RED,
                  annotation_text="75% threshold", annotation_position="top right")
    fig.update_layout(**PLOTLY_THEME, height=200)
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
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #F6F8FB; --card: #FFFFFF; --border: #E3E8EF;
    --text: #1A202C; --muted: #6B7280;
    --blue: #1A6BFF; --green: #21C55D; --red: #FF4B4B;
    --font: Inter, system-ui, sans-serif;
  }
  body { background: var(--bg); color: var(--text); font-family: var(--font);
         font-size: 14px; line-height: 1.5; padding: 0 0 2rem; }
  header { background: var(--card); border-bottom: 1px solid var(--border);
           padding: 0.9rem 2rem; display: flex; align-items: center;
           justify-content: space-between; }
  header h1 { font-size: 1.1rem; font-weight: 700; letter-spacing: -0.01em; }
  header .sub { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }
  .refresh-btn { background: var(--blue); color: #fff; border: none;
                 border-radius: 6px; padding: 0.45rem 1rem;
                 font-size: 0.82rem; font-weight: 600; cursor: pointer; }
  .refresh-btn:hover { opacity: 0.88; }
  main { max-width: 1200px; margin: 0 auto; padding: 1.5rem 1.5rem 0; }
  .demo-banner { background: #FEF3C7; border: 1px solid #F59E0B;
                 border-radius: 8px; padding: 0.6rem 1rem;
                 font-size: 0.82rem; color: #92400E; margin-bottom: 1.25rem; }
  .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr);
             gap: 1rem; margin-bottom: 1.25rem; }
  @media(max-width:768px){ .kpi-row { grid-template-columns: repeat(2, 1fr); } }
  .kpi { background: var(--card); border-radius: 10px; padding: 1rem 1.2rem;
         border-left: 4px solid var(--blue);
         box-shadow: 0 1px 3px rgba(0,0,0,.06); }
  .kpi-label { font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
               letter-spacing: .07em; color: var(--muted); margin-bottom: 4px; }
  .kpi-value { font-size: 1.9rem; font-weight: 800; line-height: 1.1;
               letter-spacing: -0.02em; }
  .kpi-sub { font-size: 0.72rem; color: var(--muted); margin-top: 3px; }
  .chart-grid-2 { display: grid; grid-template-columns: 3fr 2fr;
                  gap: 1rem; margin-bottom: 1rem; }
  .chart-grid-equal { display: grid; grid-template-columns: 1fr 1fr;
                      gap: 1rem; margin-bottom: 1rem; }
  @media(max-width:768px){
    .chart-grid-2, .chart-grid-equal { grid-template-columns: 1fr; }
  }
  .card { background: var(--card); border-radius: 10px; padding: 1rem;
          box-shadow: 0 1px 3px rgba(0,0,0,.06);
          border: 1px solid var(--border); }
  .card h3 { font-size: 0.82rem; font-weight: 700; color: var(--muted);
             text-transform: uppercase; letter-spacing: .06em;
             margin-bottom: 0.6rem; }
  .full-width { margin-bottom: 1rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
  th { background: var(--bg); color: var(--muted); font-weight: 700;
       font-size: 0.68rem; text-transform: uppercase; letter-spacing: .06em;
       padding: 0.5rem 0.75rem; text-align: left;
       border-bottom: 2px solid var(--border); }
  td { padding: 0.45rem 0.75rem; border-bottom: 1px solid var(--border); }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--bg); }
  .pill { display: inline-block; border-radius: 4px; padding: 2px 7px;
          font-size: 0.68rem; font-weight: 700; }
  .pill-green { background: #DCFCE7; color: #166534; }
  .pill-red   { background: #FEE2E2; color: #991B1B; }
  .pill-blue  { background: #DBEAFE; color: #1E40AF; }
  footer { text-align: center; color: var(--muted); font-size: 0.72rem;
           margin-top: 2rem; }
  details summary { cursor: pointer; font-size: 0.82rem; font-weight: 600;
                    color: var(--muted); padding: 0.5rem 0; user-select: none; }
  details[open] summary { margin-bottom: 0.5rem; }
</style>
</head>
<body>
<header>
  <div>
    <h1>📊 CreditBot Analytics</h1>
    <div class="sub">Application readiness metrics &nbsp;·&nbsp;
      Auto-refreshes every 60 s</div>
  </div>
  <button class="refresh-btn" onclick="location.reload()">↺ Refresh</button>
</header>
<main>

{% if is_demo %}
<div class="demo-banner">
  ⚠️ <strong>Demo mode</strong> — Supabase is not configured.
  Showing 60 synthetic sample applications. Connect Supabase to see live data.
</div>
{% endif %}

<!-- KPIs -->
<div class="kpi-row">
  <div class="kpi">
    <div class="kpi-label">Total applications</div>
    <div class="kpi-value">{{ total }}</div>
    <div class="kpi-sub">{{ proc_count }} fully processed</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Avg completeness</div>
    <div class="kpi-value">{{ avg_comp }}%</div>
    <div class="kpi-sub">across processed applications</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Ready for review</div>
    <div class="kpi-value">{{ ready_rate }}%</div>
    <div class="kpi-sub">{{ ready_count }} of {{ proc_count }}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Avg turnaround</div>
    <div class="kpi-value">{{ avg_turn }}s</div>
    <div class="kpi-sub">Claude extraction + validation</div>
  </div>
</div>

<!-- Row 1: volume + donut -->
<div class="chart-grid-2">
  <div class="card"><div id="chart-volume"></div></div>
  <div class="card"><div id="chart-ready"></div></div>
</div>

<!-- Row 2: flags + channels -->
<div class="chart-grid-equal">
  <div class="card"><div id="chart-flags"></div></div>
  <div class="card"><div id="chart-channels"></div></div>
</div>

<!-- Row 3: completeness histogram -->
<div class="full-width card">
  <div id="chart-completeness"></div>
</div>

<!-- Recent applications table -->
<div class="full-width card">
  <h3>Recent applications (last 30)</h3>
  <p style="font-size:.72rem;color:var(--muted);margin-bottom:.75rem">
    PII fields (name, phone, address) are not shown per data-handling guardrail.
  </p>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>Reference</th><th>Submitted</th><th>Complete</th>
        <th>Ready</th><th>Officer</th><th>Notified via</th><th>Turnaround</th>
      </tr>
    </thead>
    <tbody>
    {% for row in table_rows %}
    <tr>
      <td style="font-family:monospace;font-size:.75rem">{{ row.ref }}</td>
      <td>{{ row.submitted }}</td>
      <td>{{ row.completeness }}</td>
      <td>
        {% if row.ready == "Yes" %}
          <span class="pill pill-green">Yes</span>
        {% else %}
          <span class="pill pill-red">No</span>
        {% endif %}
      </td>
      <td>{{ row.officer or "—" }}</td>
      <td>
        {% if row.channel %}
          <span class="pill pill-blue">{{ row.channel }}</span>
        {% else %}—{% endif %}
      </td>
      <td>{{ row.turnaround }}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</div>

<!-- Audit log -->
<div class="full-width card">
  <details>
    <summary>🔒 Audit log (last 50 events)</summary>
    {% if audit_rows %}
    <div style="overflow-x:auto">
    <table>
      <thead>
        <tr><th>Timestamp</th><th>Event</th><th>Actor</th><th>Payload</th></tr>
      </thead>
      <tbody>
      {% for ev in audit_rows %}
      <tr>
        <td style="white-space:nowrap;font-size:.75rem">{{ ev.ts }}</td>
        <td><span class="pill pill-blue">{{ ev.event_type }}</span></td>
        <td>{{ ev.actor }}</td>
        <td style="font-family:monospace;font-size:.72rem;max-width:320px;
                   overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
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

</main>
<footer>CreditBot Analytics &nbsp;·&nbsp; This dashboard shows readiness
metrics only — no lending decisions are recorded here.</footer>

<script>
const config = {responsive: true, displayModeBar: false};
Plotly.newPlot('chart-volume',   {{ volume_json   | safe }});
Plotly.newPlot('chart-ready',    {{ ready_json    | safe }});
Plotly.newPlot('chart-flags',    {{ flags_json    | safe }});
Plotly.newPlot('chart-channels', {{ channels_json | safe }});
Plotly.newPlot('chart-completeness', {{ completeness_json | safe }});

// Auto-refresh every 60 s
setTimeout(() => location.reload(), 60000);
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

    # Charts
    volume_json      = chart_volume(apps_df) if not apps_df.empty else "{}"
    ready_json       = chart_ready(processed) if proc_count else "{}"
    flags_json       = chart_flags(processed) if proc_count else "{}"
    channels_json    = chart_channels(processed) if proc_count else "{}"
    completeness_json = chart_completeness(processed) if proc_count else "{}"

    def _extract_chart(j):
        """Plotly.newPlot expects {data, layout} not the full JSON spec."""
        obj = json.loads(j)
        return json.dumps({"data": obj.get("data", []), "layout": obj.get("layout", {})})

    # Recent applications table rows
    table_rows = []
    for _, r in apps_df.head(30).iterrows():
        ts = r["created_at"]
        table_rows.append({
            "ref":         r.get("reference_number", ""),
            "submitted":   ts.strftime("%Y-%m-%d %H:%M") if pd.notna(ts) else "—",
            "completeness": f"{r['completeness_pct']:.1f}%",
            "ready":       "Yes" if pd.notna(r.get("ready_for_underwriting")) and r["ready_for_underwriting"] else "No",
            "officer":     r.get("officer_code", "") if pd.notna(r.get("officer_code")) else "",
            "channel":     r.get("notification_channel", "") if pd.notna(r.get("notification_channel")) else "",
            "turnaround":  f"{r['turnaround_seconds']:.1f}s",
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
        table_rows=table_rows,
        audit_rows=audit_rows,
        volume_json=_extract_chart(volume_json) if proc_count or not apps_df.empty else "{}",
        ready_json=_extract_chart(ready_json) if proc_count else "{}",
        flags_json=_extract_chart(flags_json) if proc_count else "{}",
        channels_json=_extract_chart(channels_json) if proc_count else "{}",
        completeness_json=_extract_chart(completeness_json) if proc_count else "{}",
    )
    return Response(html, mimetype="text/html")


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
