import hashlib
import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
from supabase import create_client


SUPABASE_TABLE = "daily_employee_shift_timeworked_summary"
SUPABASE_BATCH_SIZE = 500

# Weekly overtime starts after 40 hours worked by one employee,
# across every role. Overtime is paid at 1.5 times the payroll
# wage of the role being worked when the hour occurs.
WEEKLY_REGULAR_HOUR_LIMIT = Decimal("40")
OVERTIME_MULTIPLIER = Decimal("1.5")

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


def round_hours(value):
    """
    Keeps minute-level clock math stable without turning it into
    a binary float.
    """
    return Decimal(str(value)).quantize(
        Decimal("0.000001"),
        rounding=ROUND_HALF_UP,
    )


def round_money(value):
    return Decimal(str(value)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def create_record_key(row):
    """
    Deterministic key for location + payroll employee id + role + shift start.
    """
    required_fields = [
        "location",
        "employee_id",
        "role",
        "shift_start",
    ]

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
        row["shift_start"],
        errors="raise",
    ).strftime("%Y-%m-%dT%H:%M:%S")

    value = "|".join([
        str(row["location"]).strip(),
        str(row["employee_id"]).strip(),
        str(row["role"]).strip(),
        shift_start,
    ])

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def as_timestamp(value):
    if pd.isna(value):
        return None

    parsed = pd.to_datetime(value, errors="coerce")

    if pd.isna(parsed):
        return None

    return parsed.isoformat()


def as_date(value):
    parsed = pd.to_datetime(value, errors="coerce")

    if pd.isna(parsed):
        return None

    return parsed.strftime("%Y-%m-%d")


def as_decimal_number(value):
    if value is None or pd.isna(value):
        return None

    return float(value)


def prepare_supabase_records(df):
    """
    Converts the shift dataframe into records for
    daily_employee_shift_timeworked_summary.

    Expected table:

        record_key text primary key,
        location text not null,
        employee text not null,
        employee_id text not null,
        ext_id text,
        role text not null,
        is_active boolean,
        weekday text not null,
        date date not null,
        interval text not null,
        shift_start timestamp not null,
        shift_end timestamp not null,
        hours numeric not null,
        cumulative_hours numeric not null,
        regular_hours numeric not null,
        ot_hours numeric not null,
        hourly_wage numeric not null,
        regular_wages numeric not null,
        ot_wages numeric not null,
        shift_wages numeric not null,
        updated_at timestamptz not null default now()
    """
    updated_at = datetime.now(timezone.utc).isoformat()
    records = []

    for _, row in df.iterrows():
        is_active = row["is_active"]

        if pd.isna(is_active):
            is_active = None
        else:
            is_active = bool(is_active)

        ext_id = row["ext_id"]

        if pd.isna(ext_id) or str(ext_id).strip() == "":
            ext_id = None
        else:
            ext_id = str(ext_id).strip()

        records.append({
            "record_key": row["record_key"],
            "location": str(row["location"]).strip(),
            "employee": str(row["employee"]).strip(),
            "employee_id": str(row["employee_id"]).strip(),
            "ext_id": ext_id,
            "role": str(row["role"]).strip(),
            "is_active": is_active,
            "weekday": str(row["weekday"]).strip(),
            "date": as_date(row["date"]),
            "interval": str(row["interval"]).strip(),
            "shift_start": as_timestamp(row["shift_start"]),
            "shift_end": as_timestamp(row["shift_end"]),
            "hours": as_decimal_number(row["hours"]),
            "cumulative_hours": as_decimal_number(
                row["cumulative_hours"]
            ),
            "regular_hours": as_decimal_number(
                row["regular_hours"]
            ),
            "ot_hours": as_decimal_number(row["ot_hours"]),
            "hourly_wage": as_decimal_number(row["hourly_wage"]),
            "regular_wages": as_decimal_number(
                row["regular_wages"]
            ),
            "ot_wages": as_decimal_number(row["ot_wages"]),
            "shift_wages": as_decimal_number(row["shift_wages"]),
            "updated_at": updated_at,
        })

    return records


def parse_week_boundary(value, label):
    """
    Normalizes a week boundary to the YYYY-MM-DD form used by the
    Supabase date column.
    """
    try:
        return datetime.strptime(
            str(value).strip(),
            "%Y-%m-%d",
        ).strftime("%Y-%m-%d")
    except ValueError as error:
        raise ValueError(
            f"{label} must use YYYY-MM-DD. "
            f"Received: {value}"
        ) from error


def purge_supabase_week(
    client,
    location,
    week_start,
    week_end,
):
    """
    Deletes this location's existing rows for the reporting week.

    An upsert alone cannot remove rows that are no longer in the report,
    such as a shift that was deleted in Revel or re-keyed because its
    start time, role, or payroll ID changed. Those rows would otherwise
    survive forever as orphans.
    """
    print(
        f"Supabase: purging existing {location} rows "
        f"from {week_start} through {week_end}."
    )

    response = (
        client
        .table(SUPABASE_TABLE)
        .delete()
        .eq("location", location)
        .gte("date", week_start)
        .lte("date", week_end)
        .execute()
    )

    purged = len(response.data or [])

    print(
        f"Supabase: purged {purged} existing rows."
    )

    return purged


def write_to_supabase(
    df,
    location,
    week_start=None,
    week_end=None,
):
    """
    Replaces the reporting week for one location in Supabase.

    Existing rows for the location/week are purged first, then the final
    processed dataframe is upserted in batches.

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

    location_name = str(location or "").strip()

    if not location_name:
        raise ValueError(
            "A location is required before writing to Supabase."
        )

    # An empty dataframe means the report produced nothing, which is far
    # more likely to be a parsing failure than a week with zero shifts.
    # Returning early keeps that case from purging good rows.
    if df.empty:
        print(
            "Supabase: no rows to upload. "
            "Skipping purge and upsert."
        )
        return 0

    written_locations = sorted({
        str(value)
        for value in df["location"].dropna().unique()
    })

    if written_locations != [location_name]:
        raise ValueError(
            "Location mismatch between the purge filter and the rows "
            f"being written. Purge uses {location_name!r} but the data "
            f"contains {written_locations!r}."
        )

    records = prepare_supabase_records(df)

    client = create_client(
        supabase_url,
        supabase_key,
    )

    if week_start and week_end:
        purge_start = parse_week_boundary(
            week_start,
            "week_start",
        )

        purge_end = parse_week_boundary(
            week_end,
            "week_end",
        )

        if purge_start > purge_end:
            raise ValueError(
                "week_start must not be after week_end. "
                f"Received: {purge_start} through {purge_end}"
            )

        purge_supabase_week(
            client,
            location_name,
            purge_start,
            purge_end,
        )
    else:
        print(
            "Supabase: no reporting week supplied. "
            "Skipping purge and relying on the upsert alone."
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


def normalize_columns(df):
    """
    Collapses the line breaks Revel puts inside payroll headers.
    """
    renamed = df.copy()

    renamed.columns = [
        " ".join(str(column).replace("\n", " ").split())
        for column in renamed.columns
    ]

    return renamed


def require_columns(df, columns, report_name):
    missing = [
        column
        for column in columns
        if column not in df.columns
    ]

    if missing:
        raise ValueError(
            f"{report_name} is missing required column(s): "
            + ", ".join(missing)
            + ". Found: "
            + ", ".join(str(column) for column in df.columns)
        )


def parse_bool(value):
    if pd.isna(value):
        return pd.NA

    text = str(value).strip().lower()

    if text in {"true", "1", "yes"}:
        return True

    if text in {"false", "0", "no"}:
        return False

    return pd.NA


def format_identifier(value):
    if pd.isna(value):
        return pd.NA

    text = str(value).strip()

    if not text or text.lower() == "nan":
        return pd.NA

    if text.endswith(".0"):
        text = text[:-2]

    return text


def identify_columns(df):
    """
    Returns columns that begin with a weekday name.
    """
    return [
        col
        for col in df.columns
        if str(col).lower().startswith(WEEKDAYS)
    ]


def parse_day_column(column_name):
    """
    Extracts weekday and date from a column name such as:
        Monday 07/27/2026
    """
    parts = str(column_name).strip().split()

    if not parts:
        return None, None

    weekday = (
        parts[0]
        if parts[0].lower() in WEEKDAYS
        else None
    )

    date_value = parts[1] if len(parts) > 1 else None

    return weekday, date_value


def parse_interval(interval):
    """
    Parses a shift interval such as:
        10:27AM - 4:08PM
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


def clock_on_date(shift_start, end_time):
    """
    Places the clock-out on the shift date, rolling to the next
    date when the clock-out is after midnight.
    """
    end_clock = datetime.strptime(end_time.strip(), "%I:%M%p")
    shift_end = shift_start.replace(
        hour=end_clock.hour,
        minute=end_clock.minute,
        second=0,
        microsecond=0,
    )

    if shift_end < shift_start:
        shift_end = shift_end + pd.Timedelta(days=1)

    return shift_end


def get_shift_interval_duration(start_time, end_time):
    """
    Hours between two clock times.

    A clock-out earlier than the clock-in is treated as the next day,
    for example 10:00PM -> 2:00AM = 4 hours.
    """
    start = datetime.strptime(start_time.strip(), "%I:%M%p")
    end = datetime.strptime(end_time.strip(), "%I:%M%p")
    seconds = (end - start).total_seconds()

    if seconds < 0:
        seconds += 24 * 60 * 60

    return round_hours(seconds / 3600)


# ============================================================
# DATA LOADING
# ============================================================


def load_data(shifts_path, payroll_path):
    df_shifts = normalize_columns(
        pd.read_csv(shifts_path, dtype=str)
    )
    df_payroll = normalize_columns(
        pd.read_csv(payroll_path, dtype=str)
    )

    return df_shifts, df_payroll


# ============================================================
# SHIFTS
# ============================================================


def reshape_shift_intervals(df_shifts):
    """
    Converts each clock range in the Time Worked Shifts grid into one row.

    Hours come from those clock ranges. The report's Total Hours column
    is ignored because it does not match the ranges on the grid.
    """
    require_columns(
        df_shifts,
        ["Employee", "Role Name"],
        "Time Worked Shifts",
    )

    day_columns = identify_columns(df_shifts)

    if not day_columns:
        raise ValueError(
            "Time Worked Shifts has no weekday columns. "
            "Found: "
            + ", ".join(str(column) for column in df_shifts.columns)
        )

    interval_list = []

    for _, row in df_shifts.iterrows():
        employee = (
            str(row["Employee"]).strip()
            if pd.notna(row["Employee"])
            else ""
        )

        if "," not in employee:
            continue

        role = (
            str(row["Role Name"]).strip()
            if pd.notna(row["Role Name"])
            else ""
        )

        if not role or role.lower() == "nan":
            raise ValueError(
                "Time Worked Shifts is missing a role for "
                f"{employee}. Display Roles needs to be checked."
            )

        for column in day_columns:
            weekday, date_value = parse_day_column(column)
            cell_value = row[column]

            if pd.isna(cell_value):
                continue

            for interval in str(cell_value).split(";"):
                interval = interval.strip()

                if not interval:
                    continue

                start_time, end_time = parse_interval(interval)

                if not start_time or not end_time:
                    raise ValueError(
                        "Could not read shift "
                        f"'{interval}' for {employee} "
                        f"on {column}."
                    )

                interval_list.append({
                    "employee": employee,
                    "role": role,
                    "interval": interval,
                    "weekday": weekday,
                    "date": date_value,
                    "start_time": start_time,
                    "end_time": end_time,
                    "hours": get_shift_interval_duration(
                        start_time,
                        end_time,
                    ),
                })

    shifts = pd.DataFrame(interval_list)

    if shifts.empty:
        raise ValueError(
            "Time Worked Shifts did not contain any clock ranges."
        )

    shifts["date"] = pd.to_datetime(
        shifts["date"],
        errors="coerce",
    )

    shifts["shift_start"] = pd.to_datetime(
        (
            shifts["date"].dt.strftime("%Y-%m-%d")
            + " "
            + shifts["start_time"]
        ),
        format="%Y-%m-%d %I:%M%p",
        errors="coerce",
    )

    invalid = shifts[
        shifts["date"].isna()
        | shifts["shift_start"].isna()
        | shifts["weekday"].isna()
    ]

    if not invalid.empty:
        sample = invalid.iloc[0]
        raise ValueError(
            "Could not build a shift timestamp for "
            f"{sample['employee']} interval '{sample['interval']}'."
        )

    shifts["shift_end"] = [
        clock_on_date(start, end_time)
        for start, end_time in zip(
            shifts["shift_start"],
            shifts["end_time"],
        )
    ]

    shifts = (
        shifts
        .sort_values(
            by=["employee", "shift_start", "role", "interval"]
        )
        .reset_index(drop=True)
    )

    return shifts


# ============================================================
# PAYROLL
# ============================================================


def clean_payroll(df_payroll):
    """
    Keeps one wage row per employee and role.

    The blank-role row is the employee total. Expand Roles is what
    produces the role rows this function uses.
    """
    require_columns(
        df_payroll,
        ["Employee", "Role", "ID", "Wage"],
        "Payroll",
    )

    payroll = df_payroll[
        df_payroll["Employee"].notna()
        & df_payroll["Role"].notna()
    ].copy()

    payroll["employee"] = (
        payroll["Employee"].astype("string").str.strip()
    )
    payroll["role"] = (
        payroll["Role"].astype("string").str.strip()
    )

    payroll = payroll[
        payroll["employee"].ne("")
        & payroll["role"].ne("")
        & payroll["role"].str.lower().ne("nan")
        & payroll["employee"].str.lower().ne("nan")
    ].copy()

    if payroll.empty:
        raise ValueError(
            "Payroll did not contain any role rows. "
            "Expand Roles needs to be checked."
        )

    payroll["employee_id"] = payroll["ID"].map(format_identifier)
    payroll["ext_id"] = (
        payroll["Ext. ID"].map(format_identifier)
        if "Ext. ID" in payroll.columns
        else pd.NA
    )
    payroll["is_active"] = (
        payroll["Is Active"].map(parse_bool)
        if "Is Active" in payroll.columns
        else pd.NA
    )
    payroll["hourly_wage"] = pd.to_numeric(
        payroll["Wage"],
        errors="coerce",
    )

    for source_name, target_name in (
        ("Regular h.", "payroll_regular_hours"),
        ("Overtime h.", "payroll_overtime_hours"),
        ("Doubletime h.", "payroll_doubletime_hours"),
    ):
        if source_name in payroll.columns:
            payroll[target_name] = pd.to_numeric(
                payroll[source_name],
                errors="coerce",
            ).fillna(0)
        else:
            payroll[target_name] = 0

    missing_identity = payroll[
        payroll["employee_id"].isna()
        | payroll["hourly_wage"].isna()
    ]

    if not missing_identity.empty:
        sample = missing_identity.iloc[0]
        raise ValueError(
            "Payroll role row is missing an ID or wage for "
            f"{sample['employee']} / {sample['role']}."
        )

    payroll["hourly_wage"] = payroll["hourly_wage"].map(
        lambda value: round_money(value)
    )

    duplicate_wages = (
        payroll
        .groupby(["employee", "role"])["hourly_wage"]
        .nunique()
    )
    conflicting = duplicate_wages[duplicate_wages > 1]

    if not conflicting.empty:
        employee, role = conflicting.index[0]
        raise ValueError(
            "Payroll has more than one wage for "
            f"{employee} / {role}."
        )

    payroll = (
        payroll
        .sort_values(["employee", "role"])
        .drop_duplicates(["employee", "role"], keep="first")
        .reset_index(drop=True)
    )

    doubletime = payroll[
        payroll["payroll_doubletime_hours"] > 0
    ]

    if not doubletime.empty:
        names = ", ".join(
            sorted(doubletime["employee"].unique())
        )
        print(
            "Payroll includes doubletime hours for "
            f"{names}. Shift wages still use weekly overtime "
            "at 1.5x after 40 hours, because the shift grid "
            "does not identify which hours are doubletime."
        )

    return payroll[
        [
            "employee",
            "role",
            "employee_id",
            "ext_id",
            "is_active",
            "hourly_wage",
            "payroll_regular_hours",
            "payroll_overtime_hours",
            "payroll_doubletime_hours",
        ]
    ]


def attach_payroll_wages(shifts, payroll):
    merged = shifts.merge(
        payroll,
        on=["employee", "role"],
        how="left",
        validate="many_to_one",
    )

    unmatched = merged[merged["hourly_wage"].isna()]

    if not unmatched.empty:
        pairs = sorted({
            f"{row.employee} / {row.role}"
            for row in unmatched.itertuples(index=False)
        })
        raise ValueError(
            "No payroll wage for: " + ", ".join(pairs)
        )

    return merged


def warn_when_hours_differ_from_payroll(df):
    """
    Payroll hours are not copied onto the shift rows. This only reports
    employees whose clock-range total is more than 3 minutes away from
    the payroll role total.
    """
    comparison = df.copy()
    comparison["payroll_hours"] = (
        comparison["payroll_regular_hours"]
        + comparison["payroll_overtime_hours"]
        + comparison["payroll_doubletime_hours"]
    )

    calculated = (
        comparison
        .groupby(["employee", "role"], sort=False)
        .agg(
            calculated_hours=("hours", "sum"),
            payroll_hours=("payroll_hours", "max"),
        )
        .reset_index()
    )

    calculated["gap"] = (
        calculated["calculated_hours"].map(
            lambda value: Decimal(str(float(value)))
        )
        - calculated["payroll_hours"].map(
            lambda value: Decimal(str(value))
        )
    ).abs()

    mismatches = calculated[
        calculated["gap"] > Decimal("0.05")
    ]

    if mismatches.empty:
        print(
            "Calculated shift hours are within 0.05 of "
            "payroll role hours for every employee."
        )
        return

    print(
        "Calculated shift hours differ from payroll "
        "role hours by more than 0.05:"
    )

    for row in mismatches.itertuples(index=False):
        print(
            f"  {row.employee} / {row.role}: "
            f"shifts {float(row.calculated_hours):.2f}, "
            f"payroll {float(row.payroll_hours):.2f}"
        )


# ============================================================
# HOURS AND WAGES
# ============================================================


def allocate_weekly_overtime(df):
    """
    Splits each shift into regular and overtime hours.

    Hours are accumulated in clock order for the employee, across roles.
    Time through 40 hours is regular. Time after that is overtime.
    """
    shifts = (
        df
        .sort_values(
            by=["employee_id", "shift_start", "role", "interval"]
        )
        .reset_index(drop=True)
    )

    regular_hours = []
    overtime_hours = []
    cumulative_hours = []
    hours_before_shift = {}

    for row in shifts.itertuples(index=False):
        already_worked = hours_before_shift.get(
            row.employee_id,
            Decimal("0"),
        )
        duration = row.hours
        remaining_regular = (
            WEEKLY_REGULAR_HOUR_LIMIT - already_worked
        )

        if remaining_regular <= 0:
            regular = Decimal("0")
        else:
            regular = min(duration, remaining_regular)

        overtime = duration - regular
        worked_through = already_worked + duration

        regular_hours.append(regular)
        overtime_hours.append(overtime)
        cumulative_hours.append(worked_through)
        hours_before_shift[row.employee_id] = worked_through

    shifts["regular_hours"] = regular_hours
    shifts["ot_hours"] = overtime_hours
    shifts["cumulative_hours"] = cumulative_hours

    return shifts


def calculate_shift_wages(df):
    """
    Prices each shift from the payroll hourly wage.

    Regular hours use the role wage. Overtime hours use 1.5 times
    that same wage.
    """
    priced = df.copy()

    priced["regular_wages"] = [
        round_money(hours * wage)
        for hours, wage in zip(
            priced["regular_hours"],
            priced["hourly_wage"],
        )
    ]
    priced["ot_wages"] = [
        round_money(hours * wage * OVERTIME_MULTIPLIER)
        for hours, wage in zip(
            priced["ot_hours"],
            priced["hourly_wage"],
        )
    ]
    priced["shift_wages"] = [
        regular + overtime
        for regular, overtime in zip(
            priced["regular_wages"],
            priced["ot_wages"],
        )
    ]

    return priced


# ============================================================
# MAIN PIPELINE
# ============================================================


def main(shifts_path, payroll_path, location):
    df_shifts, df_payroll = load_data(
        shifts_path,
        payroll_path,
    )

    shifts = reshape_shift_intervals(df_shifts)
    payroll = clean_payroll(df_payroll)
    shifts = attach_payroll_wages(shifts, payroll)
    warn_when_hours_differ_from_payroll(shifts)
    shifts = allocate_weekly_overtime(shifts)
    shifts = calculate_shift_wages(shifts)

    location_name = str(location or "").strip()

    if not location_name:
        raise ValueError("A location is required.")

    shifts["location"] = location_name
    shifts["record_key"] = shifts.apply(
        create_record_key,
        axis=1,
    )

    duplicate_keys = shifts["record_key"].duplicated()

    if duplicate_keys.any():
        sample = shifts.loc[duplicate_keys].iloc[0]
        raise ValueError(
            "Two shifts produced the same record_key for "
            f"{sample['employee']} / {sample['role']} "
            f"starting {sample['shift_start']}."
        )

    shifts = shifts.sort_values(
        ["employee", "shift_start", "role"]
    ).reset_index(drop=True)

    print(
        f"Processed {len(shifts)} shifts "
        f"for {shifts['employee_id'].nunique()} employees "
        f"at {location_name}. "
        f"Calculated hours "
        f"{float(sum(shifts['hours'], Decimal('0'))):.2f}. "
        f"Calculated wages "
        f"{float(sum(shifts['shift_wages'], Decimal('0'))):.2f}."
    )

    return shifts[
        [
            "record_key",
            "location",
            "employee",
            "employee_id",
            "ext_id",
            "role",
            "is_active",
            "weekday",
            "date",
            "interval",
            "shift_start",
            "shift_end",
            "hours",
            "cumulative_hours",
            "regular_hours",
            "ot_hours",
            "hourly_wage",
            "regular_wages",
            "ot_wages",
            "shift_wages",
        ]
    ]


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    import sys

    shifts_path = sys.argv[1]
    payroll_path = sys.argv[2]
    output_path = sys.argv[3]
    location = sys.argv[4]

    week_start = (
        sys.argv[5].strip()
        if len(sys.argv) >= 6
        else None
    )

    week_end = (
        sys.argv[6].strip()
        if len(sys.argv) >= 7
        else None
    )

    final_df = main(
        shifts_path,
        payroll_path,
        location,
    )

    export_df = final_df.copy()

    for column in (
        "hours",
        "cumulative_hours",
        "regular_hours",
        "ot_hours",
        "hourly_wage",
        "regular_wages",
        "ot_wages",
        "shift_wages",
    ):
        export_df[column] = export_df[column].map(
            lambda value: format(value, "f")
        )

    export_df.to_csv(
        output_path,
        index=False,
    )
    print(
        f"Processed CSV written to: {output_path}"
    )

    write_to_supabase(
        final_df,
        location,
        week_start,
        week_end,
    )
