import pandas as pd
import numpy as np
from datetime import datetime, timezone
import hashlib
import os

from supabase import create_client


SUPABASE_TABLE = "daily_employee_shift_timeworked_summary"
SUPABASE_BATCH_SIZE = 500


WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


# ============================================================
# GENERAL HELPERS
# ============================================================


def create_record_key(row):
    """
    Creates a deterministic key for:
        Location + Employee ID + Role + Shift Start

    Shift_Start is used instead of the raw Start_Interval because
    Start_Interval contains only the clock time in this dataset.
    Shift_Start contains both the date and start time.
    """
    required_fields = ["Location", "ID", "Role", "Shift_Start"]

    missing_fields = [
        field
        for field in required_fields
        if pd.isna(row[field]) or str(row[field]).strip() == ""
    ]

    if missing_fields:
        raise ValueError(
            "Cannot create record_key. Missing required field(s): "
            + ", ".join(missing_fields)
        )

    shift_start = pd.to_datetime(
        row["Shift_Start"],
        errors="raise",
    ).strftime("%Y-%m-%dT%H:%M:%S")

    value = "|".join([
        str(row["Location"]).strip(),
        str(row["ID"]).strip(),
        str(row["Role"]).strip(),
        shift_start,
    ])

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def prepare_supabase_records(df):
    """
    Converts the final TimeWorked dataframe into records whose field names
    and data types match the Supabase table.
    """
    db_df = df.copy()

    db_df = db_df.rename(
        columns={
            "Employee": "employee",
            "Role": "role",
            "Interval": "interval",
            "Weekday": "weekday",
            "Date": "date",
            "Start_Interval": "start_interval",
            "End_Interval": "end_interval",
            "Duration": "duration",
            "Total Week Hours": "total_week_hours",
            "Total Week Wage": "total_week_wage",
            "Shift_Start": "shift_start",
            "Cumulative_Hours_Duration": "cumulative_hours_duration",
            "Regular_Hours": "regular_hours",
            "OT_Hours": "ot_hours",
            "ID": "id",
            "Wages Earned": "wages_earned",
            "Weighted_Hours": "weighted_hours",
            "Daily_Role_Wages": "daily_role_wages",
            "Daily_Role_Weighted_Hours": "daily_role_weighted_hours",
            "Role_Hourly_Rate": "role_hourly_rate",
            "Shift_Regular_Wages": "shift_regular_wages",
            "Shift_OT_Wages": "shift_ot_wages",
            "Shift_Wages": "shift_wages",
            "Location": "location",
            "Shift_End": "shift_end",
        }
    )

    # The Supabase table currently defines start_interval/end_interval as
    # TIMESTAMP columns. The CSV Start_Interval/End_Interval fields contain
    # only clock times, so store their corresponding complete timestamps.
    db_df["start_interval"] = pd.to_datetime(
        db_df["shift_start"],
        errors="coerce",
    )

    db_df["end_interval"] = pd.to_datetime(
        db_df["shift_end"],
        errors="coerce",
    )

    # PostgreSQL DATE field.
    db_df["date"] = pd.to_datetime(
        db_df["date"],
        errors="coerce",
    ).dt.strftime("%Y-%m-%d")

    # PostgreSQL TIMESTAMP fields.
    timestamp_columns = [
        "start_interval",
        "end_interval",
        "shift_start",
        "shift_end",
    ]

    for column in timestamp_columns:
        db_df[column] = pd.to_datetime(
            db_df[column],
            errors="coerce",
        ).apply(
            lambda value: (
                value.isoformat()
                if pd.notna(value)
                else None
            )
        )

    # updated_at has a DEFAULT on insert, but PostgreSQL does not
    # automatically change it on UPDATE. Set it explicitly for upserts.
    db_df["updated_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    # Replace pandas missing values with JSON-compatible None.
    db_df = db_df.astype(object).where(
        pd.notnull(db_df),
        None,
    )

    return db_df.to_dict(
        orient="records"
    )


def write_to_supabase(df):
    """
    Upserts the final processed dataframe into Supabase in batches.

    Required Apify runtime environment variables:
        SUPABASE_URL
        SUPABASE_SERVICE_ROLE_KEY
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        raise RuntimeError(
            "Missing required environment variable: SUPABASE_URL"
        )

    if not supabase_key:
        raise RuntimeError(
            "Missing required environment variable: "
            "SUPABASE_SERVICE_ROLE_KEY"
        )

    if df.empty:
        print("Supabase: no rows to upload.")
        return 0

    records = prepare_supabase_records(df)

    client = create_client(
        supabase_url,
        supabase_key,
    )

    total_uploaded = 0

    for start in range(
        0,
        len(records),
        SUPABASE_BATCH_SIZE,
    ):
        batch = records[
            start:start + SUPABASE_BATCH_SIZE
        ]

        (
            client
            .table(SUPABASE_TABLE)
            .upsert(
                batch,
                on_conflict="record_key",
            )
            .execute()
        )

        total_uploaded += len(batch)

        print(
            f"Supabase: upserted "
            f"{total_uploaded}/{len(records)} rows."
        )

    print(
        f"Supabase upload complete: "
        f"{total_uploaded} rows upserted into "
        f"{SUPABASE_TABLE}."
    )

    return total_uploaded


def remove_non_employee_rows(df):
    """
    Keeps rows where Employee appears to be in 'Last, First' format.
    """
    df = df.copy()

    df["Employee"] = (
        df["Employee"]
        .astype("string")
        .str.strip()
    )

    df = df[
        df["Employee"].str.contains(",", na=False)
    ].reset_index(drop=True)

    return df


def identify_columns(df):
    """
    Returns columns that begin or end with a weekday name.
    """
    return [
        col
        for col in df.columns
        if str(col).lower().startswith(WEEKDAYS)
        or str(col).lower().endswith(WEEKDAYS)
    ]


def parse_day_column(column_name):
    """
    Extracts weekday and date from a column name such as:
        Monday 9/7/2026

    Returns:
        weekday, date_string
    """
    col = str(column_name).strip()
    parts = col.split()

    if not parts:
        return None, None

    weekday = (
        parts[0].lower()
        if parts[0].lower() in WEEKDAYS
        else None
    )

    date_value = parts[1] if len(parts) > 1 else None

    return weekday, date_value


def parse_interval(interval):
    """
    Parses a shift interval such as:
        8:00AM-2:00PM

    Returns:
        start_time, end_time
    """
    if pd.isna(interval):
        return None, None

    interval = str(interval).strip()

    if not interval or "-" not in interval:
        return None, None

    start_time, end_time = interval.split("-", 1)

    start_time = start_time.strip()
    end_time = end_time.strip()

    if not start_time or not end_time:
        return None, None

    return start_time, end_time


def get_shift_interval_duration(start_time, end_time):
    """
    Calculates shift duration in hours.

    Handles overnight shifts, for example:
        10:00PM -> 2:00AM = 4 hours
    """
    if not start_time or not end_time:
        return np.nan

    try:
        start = datetime.strptime(start_time.strip(), "%I:%M%p")
        end = datetime.strptime(end_time.strip(), "%I:%M%p")

        seconds = (end - start).total_seconds()

        if seconds < 0:
            seconds += 24 * 60 * 60

        return seconds / 3600

    except (ValueError, AttributeError, TypeError):
        return np.nan


# ============================================================
# DATA LOADING
# ============================================================

def load_data(shifts_path,wages_path,payroll_path):
    df_shifts = pd.read_csv(shifts_path)
    df_wages = pd.read_csv(wages_path)
    df_payroll = pd.read_csv(payroll_path)

    return df_shifts, df_wages, df_payroll


# ============================================================
# SHIFTS
# ============================================================

def clean_shifts(df_shifts):
    df = df_shifts.copy()

    df["Employee"] = df["Employee"].astype("string").str.strip()
    df["Role Name"] = df["Role Name"].astype("string").str.strip()

    df["Total Hours"] = pd.to_numeric(
        df["Total Hours"],
        errors="coerce"
    )

    df["Total Wage"] = pd.to_numeric(
        df["Total Wage"],
        errors="coerce"
    )

    # Calculate implied base hourly rate.
    #
    # If Total Hours > 40:
    #
    # Total Wage =
    #   40 * rate
    #   + overtime_hours * rate * 1.5
    #
    regular_hours = df["Total Hours"].clip(upper=40)
    overtime_hours = (df["Total Hours"] - 40).clip(lower=0)

    weighted_hours = (
        regular_hours
        + overtime_hours * 1.5
    )

    df["Hourly Rate"] = np.where(
        weighted_hours > 0,
        df["Total Wage"] / weighted_hours,
        np.nan
    )

    # Round hourly rate to nearest $0.25
    df["Hourly Rate"] = (
        df["Hourly Rate"] * 4
    ).round() / 4

    df["Regular Hours"] = regular_hours
    df["Overtime Hours"] = overtime_hours

    df["Regular Wage"] = (
        df["Hourly Rate"]
        * df["Regular Hours"]
    ).round(2)

    df["Overtime Wage"] = (
        df["Hourly Rate"]
        * df["Overtime Hours"]
        * 1.5
    ).round(2)

    return df


def reshape_shift_intervals(df_shifts):
    """
    Converts weekday/date shift columns into one row per shift interval.
    """
    interval_list = []

    day_columns = identify_columns(df_shifts)

    for _, row in df_shifts.iterrows():

        employee = (
            str(row["Employee"]).strip()
            if pd.notna(row["Employee"])
            else None
        )

        role = (
            str(row["Role Name"]).strip()
            if pd.notna(row["Role Name"])
            else None
        )

        for col in day_columns:

            weekday, date_value = parse_day_column(col)

            cell_value = row[col]

            if pd.isna(cell_value):
                continue

            intervals = str(cell_value).split(";")

            for interval in intervals:

                interval = interval.strip()

                if not interval:
                    continue

                start_time, end_time = parse_interval(interval)

                if not start_time or not end_time:
                    continue

                duration = get_shift_interval_duration(
                    start_time,
                    end_time
                )

                interval_list.append(
                    {
                        "Employee": employee,
                        "Role": role,
                        "Interval": interval,
                        "Weekday": weekday,
                        "Date": date_value,
                        "Start_Interval": start_time,
                        "End_Interval": end_time,
                        "Duration": duration,
                        "Total Week Hours": (
                            row["Total Hours"]
                            if pd.notna(row["Total Hours"])
                            else np.nan
                        ),
                        "Total Week Wage": (
                            row["Total Wage"]
                            if pd.notna(row["Total Wage"])
                            else np.nan
                        ),
                    }
                )

    new_df = pd.DataFrame(interval_list)

    if new_df.empty:
        return new_df

    new_df["Date"] = pd.to_datetime(
        new_df["Date"],
        errors="coerce"
    )

    # Build a real shift start datetime so chronological sorting
    # works correctly.
    new_df["Shift_Start"] = pd.to_datetime(
        (
            new_df["Date"].dt.strftime("%Y-%m-%d")
            + " "
            + new_df["Start_Interval"]
        ),
        format="%Y-%m-%d %I:%M%p",
        errors="coerce"
    )

    new_df = (
        new_df
        .dropna(
            subset=[
                "Employee",
                "Date",
                "Shift_Start",
                "Duration",
            ]
        )
        .sort_values(
            by=[
                "Employee",
                "Shift_Start",
            ]
        )
        .reset_index(drop=True)
    )

    return new_df


# ============================================================
# OVERTIME ALLOCATION
# ============================================================

def calculate_interval_overtime(df):
    """
    Allocates regular and overtime hours chronologically.

    Example:
        Employee starts shift at 38 cumulative weekly hours.
        Shift duration = 6 hours.

        Regular = 2
        OT      = 4
    """
    df = df.copy()

    df["Cumulative_Hours_Duration"] = (
        df.groupby("Employee")["Duration"]
        .cumsum()
    )

    previous_cumulative = (
        df["Cumulative_Hours_Duration"]
        - df["Duration"]
    )

    df["Regular_Hours"] = np.where(
        previous_cumulative >= 40,
        0,
        np.minimum(
            df["Duration"],
            (40 - previous_cumulative).clip(lower=0)
        )
    )

    df["OT_Hours"] = (
        df["Duration"]
        - df["Regular_Hours"]
    )

    return df


# ============================================================
# PAYROLL
# ============================================================

def clean_payroll(df_payroll):
    df = df_payroll.copy()

    df = df[
        df["Role"].notna()
        & df["Employee"].notna()
    ].copy()

    df["Employee"] = (
        df["Employee"]
        .astype("string")
        .str.strip()
    )

    df["Role"] = (
        df["Role"]
        .astype("string")
        .str.strip()
    )

    df["ID"] = pd.to_numeric(
        df["ID"],
        errors="coerce"
    ).astype("Int64")

    df = df[
        ["Employee", "Role", "ID"]
    ].drop_duplicates()

    return df


def merge_payroll_ids(df_intervals, df_payroll):
    df = df_intervals.merge(
        df_payroll,
        on=[
            "Employee",
            "Role",
        ],
        how="left",
        validate="many_to_one"
    )

    # Remove GM rows, matching original behavior
    df = df[
        df["Role"] != "GM"
    ].reset_index(drop=True)

    return df


# ============================================================
# DAILY WAGES
# ============================================================

def clean_wages(df_wages):
    df = remove_non_employee_rows(df_wages)

    df["Role Name"] = (
        df["Role Name"]
        .astype("string")
        .str.strip()
    )

    return df


def reshape_daily_wages(df_wages):
    """
    Converts daily wage columns into one row per:
        Employee
        Role
        Date
    """
    wage_list = []

    day_columns = identify_columns(df_wages)

    for _, row in df_wages.iterrows():

        employee = (
            str(row["Employee"]).strip()
            if pd.notna(row["Employee"])
            else None
        )

        role = (
            str(row["Role Name"]).strip()
            if pd.notna(row["Role Name"])
            else None
        )

        for col in day_columns:

            weekday, date_value = parse_day_column(col)

            wage_list.append(
                {
                    "Employee": employee,
                    "Role": role,
                    "Wages Earned": row[col],
                    "Weekday": weekday,
                    "Date": date_value,
                }
            )

    df = pd.DataFrame(wage_list)

    df["Wages Earned"] = pd.to_numeric(
        df["Wages Earned"],
        errors="coerce"
    )

    df["Date"] = pd.to_datetime(
        df["Date"],
        errors="coerce"
    )

    df = (
        df
        .dropna(
            subset=[
                "Employee",
                "Role",
                "Date",
                "Wages Earned",
            ]
        )
        .reset_index(drop=True)
    )

    return df


# ============================================================
# MERGE SHIFT + WAGE DATA
# ============================================================

def merge_shift_wages(df_intervals, df_wages):
    """
    Matches each shift interval to the daily wages for the
    Employee / Role / Date.
    """
    final_df = df_intervals.merge(
        df_wages,
        on=[
            "Employee",
            "Role",
            "Weekday",
            "Date",
        ],
        how="inner"
    )

    return final_df


# ============================================================
# SHIFT WAGE ALLOCATION
# ============================================================

def calculate_shift_wages(df):
    """
    Calculates regular and overtime wages for each shift interval
    by Employee ID + Role + Date.
    """

    df = df.copy()

    # Weighted hours used to back into the base hourly rate.
    df["Weighted_Hours"] = (
        df["Regular_Hours"]
        + df["OT_Hours"] * 1.5
    )

    # Total wages earned for this employee + role + date.
    df["Daily_Role_Wages"] = (
        df.groupby(
            ["ID", "Role", "Date"]
        )["Wages Earned"]
        .transform("max")
    )

    # Total weighted hours for this employee + role + date.
    df["Daily_Role_Weighted_Hours"] = (
        df.groupby(
            ["ID", "Role", "Date"]
        )["Weighted_Hours"]
        .transform("sum")
    )

    # Base hourly rate for the role/day.
    df["Role_Hourly_Rate"] = np.where(
        df["Daily_Role_Weighted_Hours"] > 0,
        (
            df["Daily_Role_Wages"]
            / df["Daily_Role_Weighted_Hours"]
        ),
        np.nan
    )

    # Regular portion of this shift.
    df["Shift_Regular_Wages"] = (
        df["Role_Hourly_Rate"]
        * df["Regular_Hours"]
    ).round(2)

    # OT portion of this shift.
    df["Shift_OT_Wages"] = (
        df["Role_Hourly_Rate"]
        * 1.5
        * df["OT_Hours"]
    ).round(2)

    # Optional total shift wages.
    df["Shift_Wages"] = (
        df["Shift_Regular_Wages"]
        + df["Shift_OT_Wages"]
    ).round(2)

    return df

# ============================================================
# MAIN PIPELINE
# ============================================================

def main(shifts_path,wages_path,payroll_path,location,):

    df_shifts, df_wages, df_payroll = load_data(
        shifts_path,
        wages_path,
        payroll_path,
    )

    # --------------------------------------------------------
    # Clean source files
    # --------------------------------------------------------

    df_shifts = clean_shifts(df_shifts)
    df_wages = clean_wages(df_wages)
    df_payroll = clean_payroll(df_payroll)

    # --------------------------------------------------------
    # Convert weekly shifts into individual intervals
    # --------------------------------------------------------

    shift_intervals = reshape_shift_intervals(
        df_shifts
    )

    # --------------------------------------------------------
    # Determine regular vs overtime hours
    # --------------------------------------------------------

    shift_intervals = calculate_interval_overtime(
        shift_intervals
    )

    # --------------------------------------------------------
    # Add payroll employee ID
    # --------------------------------------------------------

    shift_intervals = merge_payroll_ids(
        shift_intervals,
        df_payroll
    )

    # --------------------------------------------------------
    # Convert wages into daily employee / role records
    # --------------------------------------------------------

    daily_wages = reshape_daily_wages(
        df_wages
    )

    # --------------------------------------------------------
    # Join shift intervals to wages
    # --------------------------------------------------------

    final_df = merge_shift_wages(
        shift_intervals,
        daily_wages
    )

    # --------------------------------------------------------
    # Allocate daily wages to individual shift intervals
    # --------------------------------------------------------

    final_df = calculate_shift_wages(
        final_df
    )

    # Optional formatting
    final_df["ID"] = (
    final_df["ID"]
    .astype("Int64")
    .astype("string")
    )

    final_df["Location"] = location

    final_df["Shift_End"] = (
        pd.to_datetime(
            final_df["Shift_Start"]
        )
        + pd.to_timedelta(
            final_df["Duration"],
            unit="h"
        )
    )

    final_df["record_key"] = final_df.apply(create_record_key, axis=1)

    final_df = final_df.sort_values(
        [
            "Employee",
            "Shift_Start",
        ]
    ).reset_index(drop=True)

    return final_df


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    import sys

    shifts_path = sys.argv[1]
    wages_path = sys.argv[2]
    payroll_path = sys.argv[3]
    output_path = sys.argv[4]
    location = sys.argv[5]

    final_df = main(
        shifts_path,
        wages_path,
        payroll_path,
        location,
    )

    final_df.to_csv(
        output_path,
        index=False,
    )
    print(
        f"Processed CSV written to: {output_path}"
    )

    # Upsert the same final dataframe into Supabase.
    write_to_supabase(final_df)