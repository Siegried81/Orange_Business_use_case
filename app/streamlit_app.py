"""
Streamlit dashboard for the Innovation Radar.

Reads radar.db (latest attractiveness and right-to-win score per opportunity
space, plus its enrichment and linked signals) and shows it as a polar radar
(domain x horizon) and a classic Attractiveness x Right-to-win bubble chart,
with role-based filters and a detail panel per opportunity space. Read-only:
it never writes to the database or calls an LLM, so it is safe to leave open
while the pipeline runs.

Run:
    python -m streamlit run app/streamlit_app.py
"""
import pandas as pd
import re
import streamlit as st
import sys
from pathlib import Path
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.db import get_connection, get_latest_scores, get_all_opportunity_spaces
from pipeline.config import (
    DOMAINS_TAXONOMY,
    HORIZONS,
    CAPABILITY_STATS,
    QUADRANTS,
    STRONG_THRESHOLD,
    quadrant,
)

st.set_page_config(
    page_title="Innovation Radar — Orange Business", layout="wide", page_icon="📡"
)
DARK_CSS = '\n<style>\n:root {\n    --radar-bg: #fffbf3;\n    --radar-panel: #f9f5f0;\n    --radar-orange: #ff7900;\n    --radar-orange-soft: #ffb066;\n    --radar-text: #002244;\n    --radar-muted: #5a6b7a;\n}\n.stApp { background-color: var(--radar-bg); color: var(--radar-text); }\nsection[data-testid="stSidebar"] { background-color: var(--radar-panel); border-right: 1px solid #e3ded2; }\nh1, h2, h3 { color: var(--radar-text) !important; }\n.stMetric { background-color: var(--radar-panel); border: 1px solid #e3ded2; border-radius: 10px; padding: 10px 14px; }\n[data-testid="stMetricValue"] { color: var(--radar-orange) !important; }\n/* st.metric labels ("Attractiveness", "Right to win", "Urgency")\n   were getting cut off with "..." in the narrow detail-panel columns. This\n   lets the label text wrap onto a second line instead of being truncated. */\n[data-testid="stMetricLabel"] p {\n    white-space: normal !important;\n    overflow: visible !important;\n    text-overflow: unset !important;\n}\n.stTabs [data-baseweb="tab"] { color: var(--radar-muted); }\n.stTabs [aria-selected="true"] { color: var(--radar-orange) !important; border-bottom-color: var(--radar-orange) !important; }\n.stAlert { background-color: var(--radar-panel); border-left: 3px solid var(--radar-orange); }\ndiv[data-testid="stDataFrame"] { border: 1px solid #e3ded2; border-radius: 8px; }\n</style>\n'
st.markdown(DARK_CSS, unsafe_allow_html=True)
HORIZON_RADIUS = {"Now": 0.28, "Next": 0.6, "Later": 0.92}
DOMAIN_NAMES = [d["name"] for d in DOMAINS_TAXONOMY]
DOMAIN_ANGLE_WIDTH = 360 / max(1, len(DOMAIN_NAMES))


@st.cache_data(ttl=60)
def load_scores():
    """Load the latest scores, enrichment columns and linked signals.

    Returns (scores dataframe, {opportunity_space_id: [signal dicts]}, total
    number of opportunity spaces including unscored ones). Cached for 60 s so
    every widget interaction does not re-query SQLite, while a pipeline run
    still shows up within a minute. Missing enrichment is filled with explicit
    "Unassigned"/"Later" labels so filters and the radar can group them.
    """
    conn = get_connection()
    rows = get_latest_scores(conn)
    df = pd.DataFrame([dict(r) for r in rows])
    total_os_count = len(get_all_opportunity_spaces(conn))
    if df.empty:
        conn.close()
        return (df, {}, total_os_count)
    extra = conn.execute(
        "SELECT id, domain, horizon, persona, buyer_persona, geography,\n                  next_action_strategist, next_action_sales, next_action_presales\n           FROM opportunity_spaces"
    ).fetchall()
    extra_df = pd.DataFrame([dict(r) for r in extra])
    df = df.merge(extra_df, on="id", how="left")
    signals_by_os = {}
    for os_id in df["id"]:
        sig_rows = conn.execute(
            "SELECT s.source_name, s.title, s.source_url, s.collected_at,\n                      s.published_date, s.signal_type, s.summary\n               FROM opportunity_signals link\n               JOIN signals s ON s.id = link.signal_id\n               WHERE link.opportunity_space_id = ?",
            (os_id,),
        ).fetchall()
        signals_by_os[os_id] = [dict(r) for r in sig_rows]
    conn.close()
    df["domain"] = df["domain"].fillna("Unassigned")
    df["horizon"] = df["horizon"].fillna("Later")
    df["buyer_persona"] = df["buyer_persona"].fillna("Unassigned")
    df["geography"] = df["geography"].fillna("Unassigned")
    return (df, signals_by_os, total_os_count)


def domain_angle(domain, seed):
    """Angle (degrees) of an opportunity space on the polar radar.

    Each business domain owns one equal slice; the deterministic jitter from
    the OS id spreads points inside the slice so they do not overlap, and
    keeps the same position between reruns. An unknown domain gets the slot
    after the last one.
    """
    try:
        idx = DOMAIN_NAMES.index(domain)
    except ValueError:
        idx = len(DOMAIN_NAMES)
    center = idx * DOMAIN_ANGLE_WIDTH
    jitter = (seed * 2654435761 % 1000 / 1000 - 0.5) * (DOMAIN_ANGLE_WIDTH * 0.7)
    return center + jitter


def radius_for(horizon, seed):
    """Radius (0-1) on the polar radar: Now is closest to the center, Later
    is on the outside. Same deterministic jitter idea as domain_angle(),
    clamped so points never leave the plot."""
    base = HORIZON_RADIUS.get(horizon, 0.6)
    jitter = (seed * 40503 % 1000 / 1000 - 0.5) * 0.12
    return max(0.05, min(0.98, base + jitter))


def sync_selection_from_click(event, label_display, widget_key="os_selectbox"):
    """Make a click on a chart point select that OS in the detail panel.

    Writes the selectbox's session_state key (so the widget follows the
    click) and the ?topic= query param (so the selection survives a reload
    and can be shared as a link). Ignores clicks without a known label.
    """
    if not event:
        return
    points = (event.get("selection") or {}).get("points") or []
    if not points:
        return
    label = points[0].get("customdata")
    if isinstance(label, (list, tuple)):
        label = label[0] if label else None
    if label and label in label_display:
        st.session_state[widget_key] = label_display[label]
        st.query_params["topic"] = label


st.sidebar.title("📡 Innovation Radar")
st.sidebar.caption("Orange Business · opportunity spaces")
role = st.sidebar.radio(
    "Role", ["Strategist / Innovator", "Sales", "Presales / Proposal"], index=0
)
df, signals_by_os, total_os_count = load_scores()
if df.empty:
    st.title("📡 Innovation Radar")
    if total_os_count == 0:
        st.warning(
            "No opportunity spaces registered yet in radar.db. Run the pipeline first:\n\n`python -m pipeline.ingest` → `python -m pipeline.analyze` → `python radar_cli.py create` → `python -m pipeline.scoring`"
        )
    else:
        st.warning(
            f"{total_os_count} opportunity space(s) registered, but none scored yet.\n\nRun `python -m pipeline.scoring`."
        )
    st.stop()
if role == "Presales / Proposal":
    df_role = df[df["portfolio_distance"].isin(["L0", "L1", "L2"])]
else:
    df_role = df
verticals = sorted(df["vertical"].dropna().unique())
domains = sorted(df["domain"].dropna().unique())
df["persona"] = df["persona"].fillna("Unassigned")
personas_available = sorted(df["persona"].dropna().unique())
buyer_personas_available = sorted(df["buyer_persona"].dropna().unique())
geographies_available = sorted(
    {
        g.strip()
        for cell in df["geography"].dropna()
        for g in cell.split(",")
        if g.strip()
    }
)


def _matches_any_geo(cell, picked):
    """True if a comma-separated geography cell shares at least one region
    with the sidebar selection -- an OS can cover several regions, so an
    exact string match would hide multi-region opportunities."""
    if not isinstance(cell, str) or not cell:
        return False
    cell_geos = {g.strip() for g in cell.split(",")}
    return bool(cell_geos & set(picked))


st.sidebar.markdown("### Filters")
picked_verticals = st.sidebar.multiselect("Vertical", verticals, default=verticals)
picked_domains = st.sidebar.multiselect("Domain", domains, default=domains)
picked_horizons = st.sidebar.multiselect("Horizon", HORIZONS, default=HORIZONS)
picked_personas = st.sidebar.multiselect(
    "Owning team (persona)",
    personas_available,
    default=personas_available,
    help="Which Orange Business team should act on this OS -- different from 'Buyer persona' below (the customer-side contact).",
)
picked_buyer_personas = st.sidebar.multiselect(
    "Buyer persona", buyer_personas_available, default=buyer_personas_available
)
picked_geographies = st.sidebar.multiselect(
    "Geography", geographies_available, default=geographies_available
)
sort_by = st.sidebar.selectbox(
    "Order",
    ["Attractiveness", "Right to win", "Urgency", "Persona + Vertical"],
    index=0,
)
sort_col = {
    "Attractiveness": "total_score",
    "Right to win": "right_to_win_score",
    "Urgency": "urgency_score",
}.get(sort_by)
filtered = df_role[
    df_role["vertical"].isin(picked_verticals)
    & df_role["domain"].isin(picked_domains)
    & df_role["horizon"].isin(picked_horizons)
    & df_role["persona"].isin(picked_personas)
    & df_role["buyer_persona"].isin(picked_buyer_personas)
    & df_role["geography"].apply(lambda c: _matches_any_geo(c, picked_geographies))
]
if sort_by == "Persona + Vertical":
    filtered = (
        filtered.assign(_persona_sort=filtered["persona"] == "Unassigned")
        .sort_values(
            ["_persona_sort", "persona", "vertical", "total_score"],
            ascending=[True, True, True, False],
        )
        .drop(columns="_persona_sort")
    )
else:
    filtered = filtered.sort_values(sort_col, ascending=False)
if filtered.empty:
    st.warning("No Opportunity Spaces match your current filters.")
    st.stop()
st.sidebar.markdown(
    f"**{len(filtered)} of {total_os_count} spaces** match this role and filter"
)
with st.sidebar.expander("📊 Orange Business at a glance"):
    for stat in CAPABILITY_STATS:
        st.caption(f"**{stat['stat']}**  \n_{stat['source']}_")
st.title("📡 Innovation Radar")
st.caption(
    f"{role} · ordered by {sort_by.lower()} · {total_os_count} opportunity spaces tracked"
)
st.subheader("📊 Overview")
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Opportunity Spaces", len(filtered))
quadrant_counts = (
    filtered.apply(
        lambda r: quadrant(r["total_score"], r["right_to_win_score"]), axis=1
    )
    .value_counts()
    .reindex(QUADRANTS, fill_value=0)
    if len(filtered)
    else pd.Series(0, index=QUADRANTS)
)
with col2:
    st.metric(
        "⭐ Strong opportunities",
        f"{quadrant_counts['strong']} / {len(filtered)}",
        help=f"Attractiveness >= {STRONG_THRESHOLD} AND Right-to-win >= {STRONG_THRESHOLD} -- same threshold as the quadrant message in the detail panel below.",
    )
with col3:
    median_attractiveness = filtered["total_score"].median()
    st.metric("Median Attractiveness", f"{median_attractiveness:.2f}/10")
with col4:
    best_index = filtered["total_score"].idxmax()
    best_opportunity = filtered.loc[best_index, "label"]
    st.metric("Top Opportunity", best_opportunity)
with col5:
    st.markdown("**By quadrant**")
    st.caption(
        f"⭐ Strong: {quadrant_counts['strong']}  \n⚠️ Needs capability: {quadrant_counts['needs_capability']}  \n💡 Moderate market: {quadrant_counts['moderate_market']}  \n📉 Low both: {quadrant_counts['low_both']}"
    )
query_topic = st.query_params.get("topic")
labels = sorted(filtered["label"].tolist())
default_index = labels.index(query_topic) if query_topic in labels else 0
label_display = {
    lbl: f"{lbl} — {filtered.loc[filtered['label'] == lbl, 'use_case'].iloc[0]} x {filtered.loc[filtered['label'] == lbl, 'technology'].iloc[0]}"
    for lbl in labels
}
col_radar, col_detail = st.columns([3, 2])
with col_radar:
    tab_radar, tab_bubble = st.tabs(["🎯 Radar (polar)", "🫧 Bubble (classic)"])
    with tab_radar:
        fig = go.Figure()
        radar_data = filtered[filtered["domain"] != "Unassigned"]
        color_vals = radar_data["right_to_win_score"].fillna(0)
        size_vals = radar_data["total_score"].fillna(0) * 2.6 + 9
        thetas = [
            domain_angle(d, seed) for d, seed in zip(radar_data["domain"], radar_data["id"])
        ]
        radii = [
            radius_for(h, seed) for h, seed in zip(radar_data["horizon"], radar_data["id"])
        ]
        fig.add_trace(
            go.Scatterpolar(
                r=radii,
                theta=thetas,
                mode="markers",
                marker=dict(
                    size=size_vals,
                    color=color_vals,
                    colorscale=[[0, "#ffe0b2"], [0.5, "#ff7900"], [1, "#7a3800"]],
                    cmin=0,
                    cmax=10,
                    colorbar=dict(
                        title=dict(text="Right to win", font=dict(color="#002244")),
                        tickfont=dict(color="#002244"),
                    ),
                    line=dict(width=1, color="#ffffff"),
                ),
                text=[
                    f"{row.label} — {row.vertical} × {row.use_case} × {row.technology}<br>Attractiveness {row.total_score:.1f} · Right to win {(f'{row.right_to_win_score:.1f}' if pd.notna(row.right_to_win_score) else 'not scored yet')} [{row.portfolio_distance}]"
                    for row in radar_data.itertuples()
                ],
                hoverinfo="text",
                customdata=radar_data["label"],
            )
        )
        fig.update_layout(
            polar=dict(
                bgcolor="#f9f5f0",
                radialaxis=dict(
                    range=[0, 1],
                    tickvals=[HORIZON_RADIUS[h] for h in HORIZONS],
                    ticktext=[h.upper() for h in HORIZONS],
                    showline=False,
                    gridcolor="#e3ded2",
                    tickfont=dict(color="#5a6b7a"),
                ),
                angularaxis=dict(
                    tickvals=[i * DOMAIN_ANGLE_WIDTH for i in range(len(DOMAIN_NAMES))],
                    ticktext=DOMAIN_NAMES,
                    direction="clockwise",
                    gridcolor="#e3ded2",
                    tickfont=dict(color="#002244", size=11),
                ),
            ),
            paper_bgcolor="#ffffff",
            font=dict(color="#002244"),
            showlegend=False,
            height=480,
            margin=dict(l=40, r=140, t=60, b=60),
        )
        radar_event = st.plotly_chart(
            fig,
            width="stretch",
            key="radar_chart",
            on_select="rerun",
            selection_mode="points",
        )
        sync_selection_from_click(radar_event, label_display)
        st.caption(
            "Ring = horizon. Sector = domain. Size = attractiveness. Color = right to win. Click a bubble to select it."
        )
    with tab_bubble:
        fig_bubble = go.Figure()
        palette = [
            "#ff7900",
            "#4fd1c5",
            "#a78bfa",
            "#f6ad55",
            "#fc8181",
            "#68d391",
            "#63b3ed",
            "#f6e05e",
        ]
        for i, v in enumerate(sorted(filtered["vertical"].dropna().unique())):
            sub = filtered[filtered["vertical"] == v]
            fig_bubble.add_trace(
                go.Scatter(
                    x=sub["total_score"],
                    y=sub["right_to_win_score"],
                    mode="markers",
                    marker=dict(
                        size=sub["market_signal_strength"].fillna(0) * 2.2 + 8,
                        color=palette[i % len(palette)],
                        line=dict(width=1, color="#ffffff"),
                    ),
                    name=v,
                    customdata=sub["label"],
                    text=[
                        f"{row.label} — {row.use_case} × {row.technology}<br>Attractiveness {row.total_score:.1f} · Right to win {(row.right_to_win_score if pd.notna(row.right_to_win_score) else 0):.1f} [{row.portfolio_distance}]"
                        for row in sub.itertuples()
                    ],
                    hoverinfo="text",
                )
            )
        fig_bubble.update_layout(
            xaxis=dict(
                title="Attractiveness",
                range=[0, 10.5],
                gridcolor="#e3ded2",
                tickfont=dict(color="#5a6b7a"),
                title_font=dict(color="#002244"),
            ),
            yaxis=dict(
                title="Right to win",
                range=[0, 10.5],
                gridcolor="#e3ded2",
                tickfont=dict(color="#5a6b7a"),
                title_font=dict(color="#002244"),
            ),
            paper_bgcolor="#ffffff",
            plot_bgcolor="#f9f5f0",
            font=dict(color="#002244"),
            legend=dict(font=dict(color="#002244"), title=dict(text="Vertical")),
            height=620,
            margin=dict(l=10, r=10, t=10, b=10),
        )
        bubble_event = st.plotly_chart(
            fig_bubble,
            width="stretch",
            key="bubble_chart",
            on_select="rerun",
            selection_mode="points",
        )
        sync_selection_from_click(bubble_event, label_display)
        st.caption(
            "X = Attractiveness. Y = Right to win. Bubble size = market signal strength (volume proxy for market potential). Color = vertical. Click a bubble to select it in the panel on the right."
        )
with col_detail:
    if not labels:
        st.info("No opportunity space matches the current filters.")
        st.stop()
    picked_display = st.selectbox(
        "Opportunity space",
        [label_display[lbl] for lbl in labels],
        index=default_index,
        key="os_selectbox",
    )
    picked_label = labels[[label_display[lbl] for lbl in labels].index(picked_display)]
    st.query_params["topic"] = picked_label
    row = filtered[filtered["label"] == picked_label].iloc[0]
    st.subheader(f"{row.label} — {row.vertical} × {row.use_case} × {row.technology}")
    st.caption(
        f"Domain: {row.domain} · Horizon: {row.horizon} · Role: {row.persona or '—'} · Buyer persona: {row.buyer_persona or '—'} · Geography: {row.geography or '—'}"
    )
    m1, m2, m3 = st.columns(3)
    m1.metric("Attractiveness", f"{row.total_score:.1f}/10")
    m2.metric(
        "Right to win",
        (
            f"{row.right_to_win_score:.1f}/10"
            if pd.notna(row.right_to_win_score)
            else "Not scored yet"
        ),
        f"[{row.portfolio_distance}]",
    )
    m3.metric(
        "Urgency",
        f"{row.urgency_score:.1f}/10" if pd.notna(row.urgency_score) else "—",
        # Describes pipeline/scoring.py::_urgency_weighted; keep in sync.
        help="Deterministic: each linked regulation signal counts in full, each buying signal (tender) fades with age, plus a novelty term; scaled against the 95th percentile across opportunity spaces, capped at 10. Answers 'is there a real deadline', separate from attractiveness.",
    )
    role_to_action = {
        "Strategist / Innovator": row.next_action_strategist,
        "Sales": row.next_action_sales,
        "Presales / Proposal": row.next_action_presales,
    }
    fallback = "No next action generated yet — re-run scoring/enrichment."
    st.markdown(f"**Do this next — {role}**")
    st.info(role_to_action.get(role) or fallback)
    with st.expander("See next action for the other roles"):
        for other_role, action in role_to_action.items():
            if other_role == role:
                continue
            st.markdown(f"**{other_role}**")
            st.caption(action or fallback)
st.divider()
tab_score, tab_evidence, tab_signals = st.tabs(
    ["Score breakdown", "Right to win", "Sources"]
)
with tab_score:
    categories = [
        "Market signal strength",
        "Source diversity",
        "Evidence quality",
        "Novelty / momentum",
        "Strategic relevance",
    ]
    values = [
        row.market_signal_strength,
        row.source_diversity,
        row.evidence_quality,
        row.novelty_momentum,
        row.strategic_relevance,
    ]
    fig_breakdown = go.Figure()
    fig_breakdown.add_trace(
        go.Scatterpolar(
            r=values + values[:1],
            theta=categories + categories[:1],
            fill="toself",
            fillcolor="rgba(255, 121, 0, 0.35)",
            line=dict(color="#ff7900", width=2),
            name="Score breakdown",
        )
    )
    fig_breakdown.update_layout(
        polar=dict(
            bgcolor="#f9f5f0",
            radialaxis=dict(visible=True, range=[0, 10], color="#5a6b7a"),
            angularaxis=dict(color="#002244"),
        ),
        paper_bgcolor="#ffffff",
        showlegend=False,
        margin=dict(l=40, r=40, t=20, b=20),
    )
    st.plotly_chart(fig_breakdown, width="stretch", key="breakdown_radar")
    st.caption(
        f"Evidence quality — {row.evidence_quality_justification or 'no justification recorded.'}"
    )
    st.caption(
        f"Strategic relevance — {row.strategic_relevance_justification or 'no justification recorded.'}"
    )
with tab_evidence:
    st.markdown(f"**Matched assets:** {row.matched_assets or 'none'}")
    st.write(row.justification or "No right-to-win justification recorded.")
with tab_evidence:
    st.markdown("#### 🧭 Strategic position")
    attractiveness = row.total_score
    right_to_win = row.right_to_win_score
    position = quadrant(attractiveness, right_to_win)
    if position is not None:
        if position == "strong":
            st.success(
                "⭐ Strong opportunity: high attractiveness and strong right-to-win."
            )
        elif position == "needs_capability":
            st.warning(
                "⚠️ Attractive opportunity, but Orange Business may need additional capabilities."
            )
        elif position == "moderate_market":
            st.info(
                "💡 Orange Business has a strong right-to-win, but market attractiveness is more moderate."
            )
        else:
            st.warning(
                "Opportunity with relatively low attractiveness and right-to-win."
            )
with tab_signals:
    sigs = signals_by_os.get(row.id, [])
    st.caption(f"{len(sigs)} grounding signal(s) linked to this opportunity space.")
    if not sigs:
        st.info("No signals linked yet — run `radar_cli.py link` first.")
    else:
        sig_df = pd.DataFrame(sigs)
        st.markdown("**Evidence over time**")

        def _fix_bare_date_with_offset(v):
            if isinstance(v, str) and re.match(
                "^\\d{4}-\\d{2}-\\d{2}[+-]\\d{2}:\\d{2}$", v
            ):
                return v[:10] + "T00:00:00" + v[10:]
            return v

        dated = sig_df.dropna(subset=["published_date"]).copy()
        if not dated.empty:
            dated["published_date"] = dated["published_date"].map(
                _fix_bare_date_with_offset
            )
            dated["parsed_date"] = pd.to_datetime(
                dated["published_date"], format="mixed", utc=True, errors="coerce"
            )
            dated = dated.dropna(subset=["parsed_date"])
        if not dated.empty:
            dated["month"] = dated["parsed_date"].dt.to_period("M").astype(str)
            monthly_counts = dated.groupby("month").size().rename("Signals")
            st.line_chart(monthly_counts, color="#ff7900")
        else:
            st.caption("No dated signals to plot yet.")

        def _pretty_signal_type(raw):
            """Display form of a signal type ("buying_signal" -> "Buying Signal")."""
            return raw.replace("_", " ").title()

        signal_types_raw = sorted(
            {s["signal_type"] for s in sigs if s.get("signal_type")}
        )
        pretty_to_raw = {_pretty_signal_type(r): r for r in signal_types_raw}
        selected_pretty = st.multiselect(
            "Filter evidence type",
            options=list(pretty_to_raw.keys()),
            default=list(pretty_to_raw.keys()),
        )
        selected_signal_types = {pretty_to_raw[p] for p in selected_pretty}
        visible_sigs = [
            s for s in sigs if s.get("signal_type") in selected_signal_types
        ]
        st.markdown("**Sources**")
        if not visible_sigs:
            st.info("No evidence matches the selected signal types.")
        else:
            for sig in visible_sigs:
                label = f"{(_pretty_signal_type(sig.get('signal_type')) if sig.get('signal_type') else 'Unknown type')} — {sig.get('source_name') or 'Unknown source'}"
                with st.expander(label):
                    st.markdown(f"**{sig.get('title') or 'Untitled'}**")
                    if sig.get("summary"):
                        st.write(sig["summary"])
                    if sig.get("collected_at"):
                        st.caption(f"Collected: {sig['collected_at']}")
                    if sig.get("source_url"):
                        st.markdown(f"[🔗 Open source]({sig['source_url']})")
st.divider()
st.subheader(f"All matching opportunity spaces ({len(filtered)})")
st.dataframe(
    filtered[
        [
            "label",
            "vertical",
            "use_case",
            "technology",
            "domain",
            "horizon",
            "total_score",
            "right_to_win_score",
            "urgency_score",
            "portfolio_distance",
        ]
    ].rename(
        columns={
            "label": "Label",
            "vertical": "Vertical",
            "use_case": "Use Case",
            "technology": "Technology",
            "domain": "Domain",
            "horizon": "Horizon",
            "total_score": "Attractiveness",
            "right_to_win_score": "Right to Win",
            "urgency_score": "Urgency",
            "portfolio_distance": "Distance",
        }
    ),
    width="stretch",
    hide_index=True,
)
st.divider()
st.caption(
    f"Orange Business Innovation Radar • Data powered by radar.db • Viewing as: {role}"
)