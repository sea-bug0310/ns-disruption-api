import streamlit as st
from supabase import create_client, Client
import pandas as pd
import plotly.express as px
from data_access import fetch_all_rows
from pipeline_coverage import prepare_gaps, coverage_summary, LOCAL_TZ

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
LINES_VIEW = "disruption_lines"             # sql/005: one row per item and line name
STATION_WIDE = "Station-wide or unnamed"    # bucket for titles that name a station, not a line
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LINE_TYPES = ["Domestic", "International"]

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
    # In Amsterdam time, like the hour and weekday columns of the views (sql/005): an item that
    # starts at 00:30 on 1 January Amsterdam time belongs to January, not to December (UTC).
    df = df[df['start_time'].notna()].copy()
    local_start = df['start_time'].dt.tz_convert(LOCAL_TZ)
    df['Year'] = local_start.dt.year
    df['Month'] = local_start.dt.strftime('%B')
    df['Year_Month'] = local_start.dt.strftime('%Y-%m')

    # Year-Week string format (e.g., '2026-W25')
    df['Year_Week'] = local_start.dt.strftime('%G-W%V')

    return df


@st.cache_data(ttl=300)
def load_data():
    """All disruptions and maintenance items (filtered per tab below)."""
    # Paged: Supabase returns at most 1000 rows per request (see data_access.py).
    rows = fetch_all_rows(supabase, DISRUPTIONS_VIEW)
    return prepare_disruptions(pd.DataFrame(rows))


@st.cache_data(ttl=300)
def load_lines():
    """One row per item and line, for the "top lines" charts."""
    # row_id is unique (id alone repeats once per line), which paging needs for a stable order.
    rows = fetch_all_rows(supabase, LINES_VIEW, order_by="row_id")
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
    df_lines_raw = load_lines()
    df_gaps = load_gaps()
    data_as_of = load_data_as_of()

# --- Freshness: independent of the sidebar filters ---
if data_as_of is not None and pd.notna(data_as_of):
    age_hours = (pd.Timestamp.now(tz="UTC") - data_as_of).total_seconds() / 3600
    st.caption(f"Data as of {data_as_of.tz_convert(LOCAL_TZ):%Y-%m-%d %H:%M} (Amsterdam time)")
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
current_year = pd.Timestamp.now(tz=LOCAL_TZ).year
default_year_index = available_years.index(current_year) if current_year in available_years else 0
selected_year = st.sidebar.selectbox("Select Year", options=available_years, index=default_year_index)

month_order = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
# Only offer months that actually have data in the selected year
months_in_year = df_raw.loc[df_raw['Year'] == selected_year, 'Month'].unique()
available_months = [m for m in month_order if m in months_in_year]
selected_months = st.sidebar.multiselect("Select Month(s)", options=available_months, default=available_months)

def in_period(frame):
    """Rows of `frame` in the selected year and months."""
    if frame.empty:
        return frame
    return frame[(frame['Year'] == selected_year) & (frame['Month'].isin(selected_months))]


def of_type(frame, disruption_type):
    if frame.empty:
        return frame
    return frame[frame['disruption_type'] == disruption_type]


def filter_line_type(df, df_lines, selected):
    """Keep only the lines of the selected type(s) and the items that have such a line.

    With both types selected nothing is filtered (items without a line name stay). With one type,
    an item is kept when at least one of its lines is of that type, so an item with a domestic and
    an international line appears under both.
    """
    if set(selected) == set(LINE_TYPES):
        return df, df_lines

    if df.empty or df_lines.empty or not selected:
        return df.iloc[0:0], df_lines.iloc[0:0]

    wanted = selected[0] == "International"
    lines = df_lines[df_lines['is_international'] == wanted]
    return df[df['id'].isin(lines['id'])], lines


selected_line_types = st.sidebar.multiselect(
    "Line type",
    options=LINE_TYPES,
    default=LINE_TYPES,
    help="Domestic: lines between Dutch stations only. International: lines with at least one "
         "stop abroad (sql/006). An item that has both kinds of line appears in both. Lines whose "
         "stops are not recognised count as domestic.",
)

df_period = in_period(df_raw)
df_lines_period = in_period(df_lines_raw)

# --- Coverage caption: when was the pipeline blind in the selected period? ---
coverage_text, long_gaps = coverage_summary(df_gaps, selected_year, selected_months)
st.caption(coverage_text)
if not long_gaps.empty:
    with st.expander("Show the gaps"):
        st.dataframe(
            pd.DataFrame({
                "Last run before the gap (Amsterdam time)":
                    long_gaps['previous_run_at'].dt.tz_convert(LOCAL_TZ).dt.strftime('%Y-%m-%d %H:%M'),
                "First run after the gap (Amsterdam time)":
                    long_gaps['run_at'].dt.tz_convert(LOCAL_TZ).dt.strftime('%Y-%m-%d %H:%M'),
                "Gap (hours)": long_gaps['gap_hours'].round(1),
            }),
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


def horizontal_bar(counts, category_col, value_col="count"):
    """Single-colour horizontal bar chart with value labels and no redundant axis titles."""
    fig = px.bar(
        counts,
        x=value_col,
        y=category_col,
        orientation="h",
        text="label",
        color_discrete_sequence=[BREAKDOWN_COLOR],
    )
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>%{text}<extra></extra>",
    )
    fig.update_layout(
        showlegend=False,
        yaxis={"categoryorder": "total ascending", "title": None},
        xaxis={"visible": False},
    )
    return fig


def ordered_bar(labels, values):
    """Vertical bars in a fixed order (hours 00-23, weekdays Mon-Sun), zeros included."""
    fig = px.bar(
        x=labels,
        y=values,
        text=[f"{v:,}" if v else "" for v in values],  # no "0" labels on empty bars
        color_discrete_sequence=[BREAKDOWN_COLOR],
    )
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{x}: %{y}<extra></extra>",
    )
    fig.update_layout(
        showlegend=False,
        xaxis_type="category",
        xaxis_title=None,
        yaxis={"visible": False},
    )
    return fig


def render_tab(df, df_lines, key, noun, duration_label, show_causes, show_upcoming, filter_note=None):
    """One tab: KPIs, trend over time and breakdowns for a set of rows.

    df_lines: the same items, one row per item and line.
    key: unique prefix for widget keys (Streamlit needs one per widget).
    noun: what the rows are called in labels, e.g. "disruptions".
    """
    if filter_note:
        st.caption(filter_note)

    if df.empty:
        st.info(f"No {noun} in the selected period" + (" and line type." if filter_note else "."))
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

    # --- Where: lines ---
    st.subheader("Where: top lines")
    lines = df_lines[df_lines['line'] != STATION_WIDE] if not df_lines.empty else df_lines

    if lines.empty:
        st.caption("No line names in the selected period.")
    else:
        left, right = st.columns(2)

        with left:
            st.markdown(f"**Top 10 lines by number of {noun}**")
            st.plotly_chart(
                horizontal_bar(top_counts(lines, 'line'), 'line'),
                use_container_width=True, key=f"{key}_lines_count",
            )

        with right:
            st.markdown("**Top 10 lines by total hours**")
            hours = (
                lines.groupby('line')['duration_hours'].sum()
                .sort_values(ascending=False).head(10)
                .rename_axis('line').reset_index(name='hours')
            )
            hours = hours[hours['hours'] > 0]
            if hours.empty:
                st.caption("No durations known for these lines.")
            else:
                hours['label'] = hours['hours'].map("{:,.0f} h".format)
                st.plotly_chart(
                    horizontal_bar(hours, 'line', value_col='hours'),
                    use_container_width=True, key=f"{key}_lines_hours",
                )

        caption = (
            f"An item counts once for each line it touches, so these numbers add up to more "
            f"than the total number of {noun}."
        )
        station_wide = df_lines.loc[df_lines['line'] == STATION_WIDE, 'id'].nunique()
        if station_wide:
            caption += f" {station_wide:,} {noun} name only a station and are not shown."
        st.caption(caption)

    st.markdown("---")

    # --- When: hour of day and weekday, Amsterdam time ---
    st.subheader("When: starts by hour and weekday (Amsterdam time)")
    left, right = st.columns(2)

    with left:
        st.markdown("**Starts by hour of day**")
        by_hour = df['start_hour'].dropna().astype(int).value_counts().reindex(range(24), fill_value=0)
        st.plotly_chart(
            ordered_bar([f"{h:02d}" for h in range(24)], list(by_hour)),
            use_container_width=True, key=f"{key}_hour",
        )

    with right:
        st.markdown("**Starts by weekday**")
        by_weekday = df['start_isodow'].dropna().astype(int).value_counts().reindex(range(1, 8), fill_value=0)
        st.plotly_chart(
            ordered_bar(WEEKDAYS, list(by_weekday)),
            use_container_width=True, key=f"{key}_weekday",
        )

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


if not selected_line_types:
    st.info("Select at least one line type in the sidebar.")
    st.stop()

# What the tabs say when they are filtered to one line type
if set(selected_line_types) == set(LINE_TYPES):
    line_filter_note = None
else:
    line_filter_note = (
        f"Only {selected_line_types[0].lower()} lines. An item with both domestic and "
        "international lines appears in both views."
    )

# 5. Tabs: unplanned disruptions and planned maintenance
tab_disruptions, tab_maintenance = st.tabs(["Unplanned disruptions", "Planned maintenance"])

with tab_disruptions:
    render_tab(
        *filter_line_type(
            of_type(df_period, 'DISRUPTION'), of_type(df_lines_period, 'DISRUPTION'), selected_line_types
        ),
        key="disruptions",
        noun="disruptions",
        duration_label="Median duration",
        show_causes=True,
        show_upcoming=False,
        filter_note=line_filter_note,
    )

with tab_maintenance:
    # Future work counts too (status 'scheduled'). No causes chart: every maintenance item has
    # the same cause ("engineering works"), so it would be one bar.
    render_tab(
        *filter_line_type(
            of_type(df_period, 'MAINTENANCE'), of_type(df_lines_period, 'MAINTENANCE'), selected_line_types
        ),
        key="maintenance",
        noun="works",
        duration_label="Median planned duration",
        show_causes=False,
        show_upcoming=True,
        filter_note=line_filter_note,
    )
