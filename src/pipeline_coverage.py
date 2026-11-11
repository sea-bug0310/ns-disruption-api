"""Text for the dashboard's pipeline coverage caption (no Streamlit in here, so it can be tested).

Input is the `ingestion_gaps` view: one row per successful pipeline run, with the time since the
previous successful run. Coverage is described by those gaps, not by "percent of expected runs"
(GitHub starts the schedule only a few times a day, see the roadmap).
"""
import pandas as pd

LONG_GAP_HOURS = 12


def prepare_gaps(rows):
    """Turn rows of `ingestion_gaps` into a DataFrame with UTC timestamps and `gap_hours`.

    The gap is recomputed from the two timestamps: the database returns it as an interval string
    ("1 day 02:03:04", "1 mon 2 days", ...), which is awkward to parse.
    """
    df = pd.DataFrame(rows)

    if df.empty:
        return df

    for column in ("run_at", "previous_run_at"):
        df[column] = pd.to_datetime(df[column], errors="coerce", utc=True, format="ISO8601")

    df["gap_hours"] = (df["run_at"] - df["previous_run_at"]).dt.total_seconds() / 3600
    return df


def _format_time(timestamp):
    return f"{timestamp.day} {timestamp:%b %H:%M}"


def coverage_summary(gaps, year, months, threshold_hours=LONG_GAP_HOURS):
    """Return (caption text, DataFrame of the long gaps in the selected period).

    `months` are English month names, as in the dashboard's month filter. A gap belongs to the
    month of the run that ended it. The run log only exists since the new pipeline went live, so
    earlier periods get an explanation instead of "no gaps".
    """
    if gaps.empty:
        return "Pipeline coverage is not tracked yet: no runs recorded.", gaps

    tracked_since = gaps["run_at"].min()
    since_text = f"{tracked_since.day} {tracked_since:%b %Y}"

    in_period = gaps[
        (gaps["run_at"].dt.year == year) & gaps["run_at"].dt.strftime("%B").isin(months)
    ]

    if in_period.empty:
        return (
            f"Pipeline coverage is tracked since {since_text}; "
            "no runs recorded in the selected period."
        ), in_period

    long_gaps = in_period[in_period["gap_hours"] > threshold_hours]
    suffix = f" (run log since {since_text})" if tracked_since.year == year else ""

    if long_gaps.empty:
        runs = f"{len(in_period):,} run" + ("" if len(in_period) == 1 else "s")
        return (
            f"Pipeline coverage{suffix}: no gaps over {threshold_hours} hours ({runs})."
        ), long_gaps

    longest = long_gaps.loc[long_gaps["gap_hours"].idxmax()]
    noun = "gap" if len(long_gaps) == 1 else "gaps"
    return (
        f"Pipeline coverage{suffix}: {len(long_gaps)} {noun} over {threshold_hours} hours, "
        f"longest {longest['gap_hours']:.0f} h "
        f"({_format_time(longest['previous_run_at'])} to {_format_time(longest['run_at'])} UTC). "
        "Disruptions in these periods may be missing."
    ), long_gaps
