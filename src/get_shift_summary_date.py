import json
import re
import sys
from datetime import datetime, timedelta


def parse_override_flag(value):
    normalized = str(value).strip().lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off", ""}:
        return False

    raise ValueError(
        "override_flag must be true or false. "
        f"Received: {value}"
    )


def revel_date_field_shift(storename, override_flag, override_date):
    """Returns the date of the last Monday as a string."""

    store_name = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        storename.strip(),
    ).strip("_").upper()

    if not store_name:
        raise ValueError(
            "A location name is required."
        )

    if override_flag:
        if not override_date or not str(override_date).strip():
            raise ValueError(
                "override_date is required when "
                "override_flag is true."
            )

        try:
            today_date = datetime.strptime(
                str(override_date).strip(),
                "%Y-%m-%d",
            )
        except ValueError as error:
            raise ValueError(
                "override_date must use YYYY-MM-DD. "
                f"Received: {override_date}"
            ) from error
    else:
        today_date = datetime.now()

    if today_date.strftime("%A") == "Monday":
        last_monday_date = today_date - timedelta(days=7)
    else:
        last_monday_date = (
            today_date
            - timedelta(
                days=today_date.weekday()
            )
        )

    full_week_range_start = (
        last_monday_date.strftime("%Y-%m-%d")
    )

    full_week_range_end = (
        last_monday_date
        + timedelta(days=6)
    )

    full_week_range_end = (
        full_week_range_end.strftime("%Y-%m-%d")
    )

    full_week_range = {
        "start_week_date":
            full_week_range_start,

        "end_week_date":
            full_week_range_end,

        "full_week_range":
            store_name
            + "_"
            + full_week_range_start.replace("-", "")
            + "_THRU_"
            + full_week_range_end.replace("-", ""),

        "revel_date_field_shift":
            last_monday_date.strftime("%Y-%m-%d"),
    }

    return full_week_range


if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        raise SystemExit(
            "Usage: get_shift_summary_date.py "
            "<location> [override_flag] [override_date]"
        )

    override_flag = False
    override_date = None

    if len(sys.argv) >= 3:
        override_flag = parse_override_flag(
            sys.argv[2]
        )

    if len(sys.argv) >= 4 and sys.argv[3].strip():
        override_date = sys.argv[3].strip()

    results = revel_date_field_shift(
        sys.argv[1],
        override_flag,
        override_date,
    )

    print(
        json.dumps(results)
    )