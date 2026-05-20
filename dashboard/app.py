"""HappyRobot Inbound Carrier Sales — Operator Dashboard.

Live metrics for the inbound voice agent. Reads from the FastAPI backend.
Designed for a freight broker ops manager to scan call volume, conversion,
pricing, sentiment, and load status at a glance.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "dev-key-change-me")
HEADERS = {"X-API-Key": API_KEY}

OUTCOME_ORDER = [
    "booked", "negotiation_failed", "declined",
    "no_match", "ineligible", "system_error", "abandoned",
]
OUTCOME_COLORS = {
    "booked": "#22c55e",            # green
    "negotiation_failed": "#f59e0b", # amber
    "declined": "#ef4444",          # red
    "no_match": "#94a3b8",          # gray
    "ineligible": "#fb7185",        # pink-red
    "system_error": "#a855f7",      # purple
    "abandoned": "#64748b",         # slate
}
SENTIMENT_COLORS = {"positive": "#22c55e", "neutral": "#94a3b8", "negative": "#ef4444"}

st.set_page_config(page_title="Inbound Carrier Sales", page_icon="🚚", layout="wide")

# ---------- Data layer ----------

@st.cache_data(ttl=15)
def fetch(path: str):
    resp = httpx.get(f"{API_BASE_URL}{path}", headers=HEADERS, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ---------- Sidebar ----------

with st.sidebar:
    st.subheader("🔌 Connection")
    st.code(API_BASE_URL, language="text")
    auto_refresh = st.toggle("Auto-refresh (15s)", value=False)
    if st.button("🔄 Refresh now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Last refresh: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    st.divider()
    st.subheader("📚 Glossary")
    st.markdown(
        "- **Booking rate** — bookings ÷ total calls\n"
        "- **Margin** — (agreed − loadboard) ÷ loadboard\n"
        "- **Rounds** — distinct carrier offers per call\n"
        "- **Conversion funnel** — call → eligible → matched → negotiated → booked"
    )

if auto_refresh:
    st.markdown("<meta http-equiv='refresh' content='15'>", unsafe_allow_html=True)

# ---------- Fetch ----------

st.title("🚚 Inbound Carrier Sales")
st.caption("Live operator dashboard — HappyRobot voice agent + FastAPI backend")

try:
    summary = fetch("/v1/metrics/summary")
    calls = fetch("/v1/calls?limit=500")
    loads = fetch("/v1/loads")
except httpx.HTTPError as exc:
    st.error(f"Could not reach API at {API_BASE_URL}: {exc}")
    st.stop()

calls_df = pd.DataFrame(calls) if calls else pd.DataFrame()
loads_df = pd.DataFrame(loads) if loads else pd.DataFrame()

# Lookup loadboard rate per call to compute margin
loadboard_by_id = {l["load_id"]: l["loadboard_rate"] for l in loads} if loads else {}

# ---------- Top KPIs ----------

total = summary["total_calls"]
booked = summary["booked"]
booking_rate = summary["booking_rate"]
avg_agreed = summary["avg_agreed_rate"]
avg_rounds = summary["avg_rounds"]

revenue_captured = sum(safe_float(c.get("agreed_rate")) for c in calls if c.get("outcome") == "booked")
margins = []
for c in calls:
    if c.get("outcome") == "booked" and c.get("load_id") in loadboard_by_id and c.get("agreed_rate"):
        lb = loadboard_by_id[c["load_id"]]
        if lb:
            margins.append((c["agreed_rate"] - lb) / lb * 100)
avg_margin = sum(margins) / len(margins) if margins else None
pos_pct = (summary["sentiments"].get("positive", 0) / total * 100) if total else 0
loads_avail = sum(1 for l in loads if not l.get("booked")) if loads else 0
loads_total = len(loads) if loads else 0

r1c1, r1c2, r1c3, r1c4 = st.columns(4)
r1c1.metric("Total calls", f"{total:,}")
r1c2.metric("Booking rate", f"{booking_rate * 100:.1f}%", help="Booked ÷ total")
r1c3.metric("Revenue captured", f"${revenue_captured:,.0f}", help="Sum of agreed rates on booked calls")
r1c4.metric("Avg margin", f"{avg_margin:+.1f}%" if avg_margin is not None else "—",
            help="Booked rate vs loadboard rate")

r2c1, r2c2, r2c3, r2c4 = st.columns(4)
r2c1.metric("Avg negotiation rounds", f"{avg_rounds:.2f}")
r2c2.metric("Avg agreed rate", f"${avg_agreed:,.0f}" if avg_agreed else "—")
r2c3.metric("Positive sentiment", f"{pos_pct:.0f}%")
r2c4.metric("Loads available", f"{loads_avail} / {loads_total}",
            help="Unbooked loads currently in the system")

st.divider()

# ---------- Conversion funnel ----------

left, right = st.columns([1, 1])

with left:
    st.subheader("📊 Conversion funnel")
    if total > 0:
        n_eligible = sum(1 for c in calls if c.get("outcome") not in ("ineligible", "system_error"))
        n_matched = sum(1 for c in calls if c.get("load_id") and c.get("outcome") not in ("no_match", "ineligible", "system_error"))
        n_negotiated = sum(1 for c in calls if (c.get("rounds") or 0) > 0 or c.get("outcome") in ("booked", "negotiation_failed", "declined"))
        n_booked = booked
        fig = go.Figure(go.Funnel(
            y=["Call received", "Eligible (FMCSA)", "Load matched", "Negotiated", "Booked"],
            x=[total, n_eligible, n_matched, n_negotiated, n_booked],
            textinfo="value+percent initial",
            marker={"color": ["#3b82f6", "#0ea5e9", "#06b6d4", "#14b8a6", "#22c55e"]},
        ))
        fig.update_layout(height=340, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No calls yet — place a test call to populate the funnel.")

with right:
    st.subheader("🎯 Outcomes")
    outcomes = summary["outcomes"] or {}
    if outcomes:
        df = pd.DataFrame([{"outcome": k, "count": v} for k, v in outcomes.items()])
        df["outcome"] = pd.Categorical(df["outcome"], categories=OUTCOME_ORDER, ordered=True)
        df = df.sort_values("outcome")
        fig = px.bar(df, x="outcome", y="count", text="count",
                     color="outcome", color_discrete_map=OUTCOME_COLORS)
        fig.update_traces(textposition="outside")
        fig.update_layout(height=340, xaxis_title=None, yaxis_title=None,
                          showlegend=False, margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No outcomes yet.")

# ---------- Sentiment + Negotiation rounds ----------

left, right = st.columns([1, 1])

with left:
    st.subheader("😊 Carrier sentiment")
    sentiments = summary["sentiments"] or {}
    if sentiments:
        df = pd.DataFrame([{"sentiment": k, "count": v} for k, v in sentiments.items()])
        fig = px.pie(df, names="sentiment", values="count", hole=0.55,
                     color="sentiment", color_discrete_map=SENTIMENT_COLORS)
        fig.update_layout(height=320, margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No sentiment data yet.")

with right:
    st.subheader("⚖️ Negotiation rounds by outcome")
    if not calls_df.empty and "rounds" in calls_df.columns:
        nego_df = calls_df.dropna(subset=["outcome"]).copy()
        nego_df["rounds"] = nego_df["rounds"].fillna(0).astype(int)
        agg = nego_df.groupby("outcome", as_index=False)["rounds"].mean()
        agg["outcome"] = pd.Categorical(agg["outcome"], categories=OUTCOME_ORDER, ordered=True)
        agg = agg.sort_values("outcome")
        fig = px.bar(agg, x="outcome", y="rounds", text=agg["rounds"].round(2),
                     color="outcome", color_discrete_map=OUTCOME_COLORS)
        fig.update_traces(textposition="outside")
        fig.update_layout(height=320, xaxis_title=None, yaxis_title="Avg rounds",
                          showlegend=False, margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough data yet.")

# ---------- Calls over time ----------

st.subheader("📈 Call volume over time")
if summary["calls_per_day"]:
    df = pd.DataFrame(summary["calls_per_day"])
    df["date"] = pd.to_datetime(df["date"])
    fig = px.bar(df, x="date", y="count", text="count")
    fig.update_traces(textposition="outside", marker_color="#3b82f6")
    fig.update_layout(height=260, xaxis_title=None, yaxis_title=None, margin=dict(t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Not enough data for a time series yet.")

st.divider()

# ---------- Loads board ----------

st.subheader("📦 Loads board")
if not loads_df.empty:
    show = loads_df[["load_id", "origin", "destination", "equipment_type", "miles", "loadboard_rate", "pickup_datetime", "commodity_type", "booked"]].copy()
    show["pickup_datetime"] = pd.to_datetime(show["pickup_datetime"]).dt.strftime("%Y-%m-%d %H:%M")
    show["loadboard_rate"] = show["loadboard_rate"].apply(lambda v: f"${v:,.0f}")
    show["status"] = show["booked"].apply(lambda b: "🔒 Booked" if b else "✅ Available")
    show = show.drop(columns=["booked"]).rename(columns={
        "load_id": "Load", "origin": "Origin", "destination": "Destination",
        "equipment_type": "Equipment", "miles": "Miles", "loadboard_rate": "Loadboard",
        "pickup_datetime": "Pickup", "commodity_type": "Commodity", "status": "Status",
    })
    st.dataframe(show, use_container_width=True, hide_index=True)
else:
    st.info("No loads in the system.")

st.divider()

# ---------- Recent calls ----------

st.subheader("📞 Recent calls")
if not calls_df.empty:
    available_outcomes = sorted(calls_df["outcome"].dropna().unique().tolist(), key=lambda o: OUTCOME_ORDER.index(o) if o in OUTCOME_ORDER else 99)
    selected = st.multiselect("Filter by outcome", options=available_outcomes, default=available_outcomes)
    view = calls_df[calls_df["outcome"].isin(selected)].copy()
    cols = ["started_at", "carrier_name", "mc_number", "load_id",
            "initial_offer", "agreed_rate", "rounds", "outcome", "sentiment"]
    view = view[[c for c in cols if c in view.columns]]
    if "started_at" in view.columns:
        view["started_at"] = pd.to_datetime(view["started_at"]).dt.strftime("%Y-%m-%d %H:%M")
    for money in ("initial_offer", "agreed_rate"):
        if money in view.columns:
            view[money] = view[money].apply(lambda v: f"${v:,.0f}" if pd.notna(v) else "—")

    def color_outcome(val):
        c = OUTCOME_COLORS.get(val, "#cbd5e1")
        return f"background-color: {c}33; color: {c}; font-weight: 600;"

    def color_sentiment(val):
        c = SENTIMENT_COLORS.get(val, "#cbd5e1")
        return f"background-color: {c}33; color: {c}; font-weight: 600;"

    styled = view.style
    if "outcome" in view.columns:
        styled = styled.applymap(color_outcome, subset=["outcome"])
    if "sentiment" in view.columns:
        styled = styled.applymap(color_sentiment, subset=["sentiment"])
    st.dataframe(styled, use_container_width=True, hide_index=True)
    st.caption(f"Showing {len(view)} of {len(calls_df)} calls")
else:
    st.info("No calls yet. Trigger an inbound call via the HappyRobot workflow to populate this.")
