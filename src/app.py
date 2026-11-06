import streamlit as st
from supabase import create_client, Client
import pandas as pd
import plotly.express as px

# 1. Page Configuration
st.set_page_config(page_title="NS Train Disruptions Dashboard", layout="wide")
st.title("🇳🇱 NS Train Disruptions Dashboard")
st.markdown("Real-time & historical insights into Dutch railway disruptions.")

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
def prepare_disruptions(df, drop_missing_start=True):
    """Parse dates and add duration / time features to raw `disruptions` rows."""
    
    if df.empty:
        return df
        
    # --- Data Processing ---
    # utc=True keeps every column timezone-aware, even when a whole column is empty.
    # format="ISO8601" stops pandas from guessing the format from the first row and
    # silently turning rows with a different precision (e.g. fractional seconds) into NaT.
    def to_utc(series):
        return pd.to_datetime(series, errors='coerce', utc=True, format='ISO8601')

    df['start_time'] = to_utc(df['start_time'])
    df['end_time'] = to_utc(df['end_time'])
    if 'expected_duration_end_time' in df.columns:
        df['end_time'] = df['end_time'].fillna(to_utc(df['expected_duration_end_time']))
    
    df['duration_minutes'] = (df['end_time'] - df['start_time']).dt.total_seconds() / 60.0
    df['duration_minutes'] = df['duration_minutes'].apply(lambda x: max(x, 0) if pd.notnull(x) else 0)
    df['duration_hours'] = df['duration_minutes'] / 60.0
    df['affected_km'] = pd.to_numeric(df['affected_km'], errors='coerce').fillna(0)
    
    # --- Time Feature Extractions ---
    if drop_missing_start:
        df = df[df['start_time'].notna()].copy()
    df['Year'] = df['start_time'].dt.year
    df['Month'] = df['start_time'].dt.strftime('%B') 
    df['Year_Month'] = df['start_time'].dt.strftime('%Y-%m') 
    
    # New: Extract Year-Week string format (e.g., '2026-W25')
    df['Year_Week'] = df['start_time'].dt.strftime('%G-W%V') 
    
    return df


@st.cache_data(ttl=300)
def load_data():
    """All historical disruptions (used by the filtered charts)."""
    response = supabase.table("disruptions").select("*").execute()
    return prepare_disruptions(pd.DataFrame(response.data))


@st.cache_data(ttl=60)
def load_active():
    """Disruptions active right now. Deliberately independent of the sidebar filters."""
    response = supabase.table("disruptions").select("*").eq("is_active", True).execute()
    return prepare_disruptions(pd.DataFrame(response.data), drop_missing_start=False)


@st.cache_data(ttl=60)
def load_data_as_of():
    """Last time the pipeline saw any disruption in the NS feed."""
    response = (
        supabase.table("disruptions")
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
    df_active = load_active()
    data_as_of = load_data_as_of()

# --- Freshness + live section: independent of the sidebar filters ---
STALE_AFTER_HOURS = 3  # the pipeline runs every ~20 min; GitHub cron can be late

if data_as_of is not None and pd.notna(data_as_of):
    age_hours = (pd.Timestamp.now(tz="UTC") - data_as_of).total_seconds() / 3600
    st.caption(f"Data as of {data_as_of:%Y-%m-%d %H:%M} UTC")
    if age_hours > STALE_AFTER_HOURS:
        st.warning(
            f"The pipeline has not seen new data for {age_hours:.0f} hours, "
            "so the numbers below may be out of date."
        )

st.header("🔴 Active right now")
st.metric(label="Active disruptions", value=len(df_active))

if df_active.empty:
    st.info("No active disruptions in the latest data.")
else:
    live_cols = [
        'title', 'disruption_type', 'start_time', 'end_time', 'from_station', 'to_station',
        'affected_km', 'consequence_description', 'cause_label',
    ]
    live_cols = [c for c in live_cols if c in df_active.columns]
    st.dataframe(
        df_active.sort_values('start_time', ascending=False, na_position='last')[live_cols],
        hide_index=True,
        use_container_width=True,
    )

st.markdown("---")

if df_raw.empty:
    st.warning("No data found in your Supabase 'disruptions' table.")
    st.stop()

# 4. Sidebar Filters
st.sidebar.header("🎯 Filter Options")

# Year Multi-select Filter
available_years = sorted(df_raw['Year'].unique(), reverse=True)
selected_year = st.sidebar.selectbox("Select Year", options=available_years, index=0)

# Month Multi-select Filter
month_order = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
# Only offer months that actually have data in the selected year
months_in_year = df_raw.loc[df_raw['Year'] == selected_year, 'Month'].unique()
available_months = [m for m in month_order if m in months_in_year]
selected_months = st.sidebar.multiselect("Select Month(s)", options=available_months, default=available_months)

#Disruption Type Multi-select Filter
if 'disruption_type' in df_raw.columns:
    available_types = sorted(df_raw['disruption_type'].dropna().unique())
    selected_types = st.sidebar.multiselect("Select Disruption Type(s)", options=available_types, default=available_types)
else:
    available_types = []
    selected_types = []


# Apply All Filters Dynamically
df = df_raw[
    (df_raw['Year'] == selected_year) & 
    (df_raw['Month'].isin(selected_months))
]
if 'disruption_type' in df.columns and selected_types:
    df = df[df['disruption_type'].isin(selected_types)]

# Fallback block
if df.empty:
    st.warning("No data matches the selected filter combinations.")
    st.stop()


# 5. KPI Metrics Row
st.header("Key Performance Indicators")

excluded_types = sorted(set(available_types) - set(selected_types))
if excluded_types:
    st.caption("Excluding: " + ", ".join(excluded_types))

col1, col2 = st.columns(2)

with col1:
    st.metric(label="Total Disruptions", value=f"{len(df):,}")

with col2:
    # Median, not mean: a few very long incidents would otherwise dominate the number.
    median_duration = df[df['duration_hours'] > 0]['duration_hours'].median()
    median_duration_val = f"{median_duration:.1f} hours" if pd.notnull(median_duration) else "n/a"
    st.metric(
        label="Median Duration",
        value=median_duration_val,
        help="Median of disruptions with a known duration. Uses the end time, or the "
             "expected end time when no end time is known. Open-ended ones are excluded.",
    )

st.markdown("---")


# 6. Interactive Time Series Breakdown with View Switching Buttons
st.header("📈 Time Trends")

# One colour per chart family. Bar length already encodes the value, so colour should not
# encode it a second time; the values are printed on the bars instead.
TREND_COLOR = "#4C9BE8"
BREAKDOWN_COLOR = "#F28E2B"
CHART_FONT_SIZE = 15  # plotly's default is 12; Streamlit's baseFontSize does not reach inside charts


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
        hovertemplate="<b>%{y}</b><br>Incidents: %{text}<extra></extra>",
    )
    fig.update_layout(
        showlegend=False,
        font={"size": CHART_FONT_SIZE},
        yaxis={"categoryorder": "total ascending", "title": None},
        xaxis={"visible": False},
    )
    return fig


view_option = st.radio("Select View", options=["By Month-Year", "By Week-Year"], horizontal=True)

if view_option == "By Month-Year":
    trend_data = df.groupby(['Year_Month']).size().reset_index(name='Disruption Count').sort_values('Year_Month')
    x_col = 'Year_Month'
else:
    trend_data = df.groupby(['Year_Week']).size().reset_index(name='Disruption Count').sort_values('Year_Week')
    x_col = 'Year_Week'

fig_trend = px.bar(
    trend_data,
    x=x_col,
    y='Disruption Count',
    text='Disruption Count',
    title="Disruptions Count Trend",
    color_discrete_sequence=[TREND_COLOR],
)
fig_trend.update_traces(textposition="outside", cliponaxis=False)
fig_trend.update_layout(
    showlegend=False,
    font={"size": CHART_FONT_SIZE},
    xaxis_type='category',
    xaxis_title=None,
    yaxis={"visible": False},
)
st.plotly_chart(fig_trend, use_container_width=True)
st.markdown("---")


# 7. Categorical Visualizations Row
st.header("Disruption Breakdowns")
col1, col2, col3 = st.columns(3)

# --- Chart 1: Disruption Type ---
with col1:
    st.subheader("Breakdown by Type")
    if 'disruption_type' in df.columns:
        type_counts = top_counts(df, 'disruption_type', n=50)
        type_total = type_counts['count'].sum()
        type_counts['label'] = [f"{c:,} ({c / type_total:.0%})" for c in type_counts['count']]
        st.plotly_chart(horizontal_bar(type_counts, 'disruption_type'), use_container_width=True)

# --- Chart 2: Consequences ---
with col2:
    st.subheader("Top 10 Consequences")

    # Use consequence_description if available, otherwise fall back to consequence_level
    consequence_col = 'consequence_description' if 'consequence_description' in df.columns else 'consequence_level'

    if consequence_col in df.columns:
        st.plotly_chart(horizontal_bar(top_counts(df, consequence_col), consequence_col), use_container_width=True)

# --- Chart 3: Top Causes ---
with col3:
    st.subheader("Top 10 Causes")
    if 'cause_label' in df.columns:
        st.plotly_chart(horizontal_bar(top_counts(df, 'cause_label'), 'cause_label'), use_container_width=True)
