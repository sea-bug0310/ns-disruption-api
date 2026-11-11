import streamlit as st
from supabase import create_client, Client
import pandas as pd
import plotly.express as px
from data_access import fetch_all_rows
from pipeline_coverage import prepare_gaps, coverage_summary

# 1. Page Configuration
st.set_page_config(page_title="NS Train Disruptions Dashboard", layout="wide")
st.title("🇳🇱 NS Train Disruptions Dashboard")
st.markdown("Insights into unplanned disruptions and planned maintenance on the Dutch railways.")

# 2. Initialize Supabase Connection
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_supabase()
except Exception as e:
    st.error("Could not connect to Supabase. Please check your Streamlit secrets!")
    st.stop()

# 3. Load Data from Supabase
# Views (see sql/): old rows (disruptions_legacy) plus rows captured by the new pipeline.
DISRUPTIONS_VIEW = "dashboard_disruptions"  # sql/004: one row per disruption / maintenance item
GAPS_VIEW = "ingestion_gaps"                # sql/003: time between successful pipeline runs

# GitHub currently starts the scheduled pipeline only a few times a day (gaps of 8+ hours are
# normal), so "stale" means no new data for half a day.
STALE_AFTER_HOURS = 12


def to_utc(series):
    # utc=True keeps every column timezone-aware, even when a whole column is empty.
    # format="ISO8601" stops pandas from guessing the format from the first row and
    # silently turning rows with a different precision (e.g. fractional seconds) into NaT.
    return pd.to_datetime(series, errors='coerce', utc=True, format='ISO8601')


def prepare_disruptions(df):
    """Parse dates and add duration / time features to raw `dashboard_disruptions` rows."""

    if df.empty:
        return df

    df['start_time'] = to_utc(df['start_time'])
    df['end_time'] = to_utc(df['end_time'])
    if 'expected_duration_end_time' in df.columns:
        df['end_time'] = df['end_time'].fillna(to_utc(df['expected_duration_end_time']))

    df['duration_minutes'] = (df['end_time'] - df['start_time']).dt.total_seconds() / 60.0
    df['duration_minutes'] = df['duration_minutes'].apply(lambda x: max(x, 0) if pd.notnull(x) else 0)
    df['duration_hours'] = df['duration_minutes'] / 60.0

    # --- Time Feature Extractions ---
    df = df[df['start_time'].notna()].copy()
    df['Year'] = df['start_time'].dt.year
    df['Month'] = df['start_time'].dt.strftime('%B')
    df['Year_Month'] = df['start_time'].dt.strftime('%Y-%m')

    # Year-Week string format (e.g., '2026-W25')
    df['Year_Week'] = df['start_time'].dt.strftime('%G-W%V')

    return df


@st.cache_data(ttl=300)
def load_data():
    """All disruptions and maintenance items (filtered per tab below)."""
    # Paged: Supabase returns at most 1000 rows per request (see data_access.py).
    rows = fetch_all_rows(supabase, DISRUPTIONS_VIEW)
    return prepare_disruptions(pd.DataFrame(rows))


@st.cache_data(ttl=300)
def load_gaps():
    """Gaps between successful pipeline runs, for the coverage caption."""
    return prepare_gaps(fetch_all_rows(supabase, GAPS_VIEW, order_by="run_at"))


@st.cache_data(ttl=60)
def load_data_as_of():
    """Last time the pipeline saw any item in the NS feed."""
    response = (
        supabase.table(DISRUPTIONS_VIEW)
        .select("last_seen_at")
        .order("last_seen_at", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return pd.to_datetime(
        response.data[0]["last_seen_at"], utc=True, errors="coerce", format="ISO8601"
    )


with st.spinner("Fetching data from Supabase..."):
    df_raw = load_data()
    df_gaps = load_gaps()
    data_as_of = load_data_as_of()

# --- Freshness: independent of the sidebar filters ---
if data_as_of is not None and pd.notna(data_as_of):
    age_hours = (pd.Timestamp.now(tz="UTC") - data_as_of).total_seconds() / 3600
    st.caption(f"Data as of {data_as_of:%Y-%m-%d %H:%M} UTC")
    if age_hours > STALE_AFTER_HOURS:
        st.warning(
            f"The pipeline has not seen new data for {age_hours:.0f} hours, "
            "so the numbers below may be out of date."
        )

if df_raw.empty:
    st.warning(f"No data found in your Supabase '{DISRUPTIONS_VIEW}' view.")
    st.stop()

# 4. Sidebar Filters (apply to both tabs)
st.sidebar.header("🎯 Filter Options")

available_years = sorted(df_raw['Year'].unique(), reverse=True)

# Planned maintenance can lie in the future, so the newest year in the data may be next year.
# Start on the current year when there is data for it.
current_year = pd.Timestamp.now(tz="UTC").year
default_year_index = available_years.index(current_year) if current_year in available_years else 0
selected_year = st.sidebar.selectbox("Select Year", options=available_years, index=default_year_index)

month_order = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
# Only offer months that actually have data in the selected year
months_in_year = df_raw.loc[df_raw['Year'] == selected_year, 'Month'].unique()
available_months = [m for m in month_order if m in months_in_year]
selected_months = st.sidebar.multiselect("Select Month(s)", options=available_months, default=available_months)

df_period = df_raw[(df_raw['Year'] == selected_year) & (df_raw['Month'].isin(selected_months))]

# --- Coverage caption: when was the pipeline blind in the selected period? ---
coverage_text, long_gaps = coverage_summary(df_gaps, selected_year, selected_months)
st.caption(coverage_text)
if not long_gaps.empty:
    with st.expander("Show the gaps"):
        st.dataframe(
            long_gaps.assign(
                gap_hours=long_gaps['gap_hours'].round(1),
                last_run_before_gap=long_gaps['previous_run_at'].dt.strftime('%Y-%m-%d %H:%M'),
                first_run_after_gap=long_gaps['run_at'].dt.strftime('%Y-%m-%d %H:%M'),
            )[['last_run_before_gap', 'first_run_after_gap', 'gap_hours']],
            hide_index=True,
            use_container_width=True,
        )

# One colour per chart family. Bar length already encodes the value, so colour should not
# encode it a second time; the values are printed on the bars instead.
TREND_COLOR = "#4C9BE8"
BREAKDOWN_COLOR = "#F28E2B"


def top_counts(frame, column, n=10):
    """Most frequent non-empty values of `column`, with a text label for the bars."""
    values = frame[column].dropna()
    values = values[values != ""]
    counts = values.value_counts().head(n).rename_axis(column).reset_index(name="count")
    counts["label"] = counts["count"].map("{:,}".format)
    return counts


def horizontal_bar(counts, category_col):
    """Single-colour horizontal bar chart with value labels and no redundant axis titles."""
    fig = px.bar(
        counts,
        x="count",
        y=category_col,
        orientation="h",
        text="label",
        color_discrete_sequence=[BREAKDOWN_COLOR],
    )
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Count: %{text}<extra></extra>",
    )
    fig.update_layout(
        showlegend=False,
        yaxis={"categoryorder": "total ascending", "title": None},
        xaxis={"visible": False},
    )
    return fig


def render_tab(df, key, noun, duration_label, show_causes, show_upcoming):
    """One tab: KPIs, trend over time and breakdowns for a set of rows.

    key: unique prefix for widget keys (Streamlit needs one per widget).
    noun: what the rows are called in labels, e.g. "disruptions".
    """
    if df.empty:
        st.info(f"No {noun} in the selected period.")
        return

    # --- KPIs ---
    columns = st.columns(3 if show_upcoming else 2)

    columns[0].metric(label=f"Total {noun}", value=f"{len(df):,}")

    # Median, not mean: a few very long incidents would otherwise dominate the number.
    median_duration = df[df['duration_hours'] > 0]['duration_hours'].median()
    columns[1].metric(
        label=duration_label,
        value=f"{median_duration:.1f} hours" if pd.notnull(median_duration) else "n/a",
        help="Median of items with a known duration. Uses the end time, or the "
             "expected end time when no end time is known. Open-ended ones are excluded.",
    )

    if show_upcoming:
        columns[2].metric(
            label="Not started yet",
            value=f"{(df['status'] == 'scheduled').sum():,}",
            help="Planned work that is in the NS feed but whose start time has not passed.",
        )

    st.markdown("---")

    # --- Trend ---
    st.subheader("📈 Time Trends")
    view_option = st.radio(
        "Select View", options=["By Month-Year", "By Week-Year"], horizontal=True, key=f"{key}_view"
    )
    x_col = 'Year_Month' if view_option == "By Month-Year" else 'Year_Week'

    trend_data = df.groupby(x_col).size().reset_index(name='Count').sort_values(x_col)
    fig_trend = px.bar(
        trend_data,
        x=x_col,
        y='Count',
        text='Count',
        color_discrete_sequence=[TREND_COLOR],
    )
    fig_trend.update_traces(textposition="outside", cliponaxis=False)
    fig_trend.update_layout(
        showlegend=False,
        xaxis_type='category',
        xaxis_title=None,
        yaxis={"visible": False},
    )
    st.plotly_chart(fig_trend, use_container_width=True, key=f"{key}_trend")

    st.markdown("---")

    # --- Breakdowns ---
    st.subheader("Breakdowns")
    chart_columns = st.columns(2 if show_causes else 1)

    # Use consequence_description if available, otherwise fall back to consequence_level
    consequence_col = 'consequence_description' if 'consequence_description' in df.columns else 'consequence_level'

    with chart_columns[0]:
        st.markdown("**Top 10 Consequences**")
        counts = top_counts(df, consequence_col)
        if counts.empty:
            st.caption("No consequence information in this period.")
        else:
            st.plotly_chart(horizontal_bar(counts, consequence_col), use_container_width=True, key=f"{key}_consequences")

    if show_causes:
        with chart_columns[1]:
            st.markdown("**Top 10 Causes**")
            counts = top_counts(df, 'cause_label')
            if counts.empty:
                st.caption("No cause information in this period.")
            else:
                st.plotly_chart(horizontal_bar(counts, 'cause_label'), use_container_width=True, key=f"{key}_causes")


# 5. Tabs: unplanned disruptions and planned maintenance
tab_disruptions, tab_maintenance = st.tabs(["Unplanned disruptions", "Planned maintenance"])

with tab_disruptions:
    render_tab(
        df_period[df_period['disruption_type'] == 'DISRUPTION'],
        key="disruptions",
        noun="disruptions",
        duration_label="Median duration",
        show_causes=True,
        show_upcoming=False,
    )

with tab_maintenance:
    # Future work counts too (status 'scheduled'). No causes chart: every maintenance item has
    # the same cause ("engineering works"), so it would be one bar.
    render_tab(
        df_period[df_period['disruption_type'] == 'MAINTENANCE'],
        key="maintenance",
        noun="works",
        duration_label="Median planned duration",
        show_causes=False,
        show_upcoming=True,
    )
