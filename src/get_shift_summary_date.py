import json
import re
import sys
from datetime import datetime, timedelta


def revel_date_field_shift(storename):
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

    today_date = datetime.now()
    today_date = datetime(2026, 9, 17, 14, 30, 0)

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
            "Usage: get_shift_summary_date.py <location>"
        )

    results = revel_date_field_shift(
        sys.argv[1]
    )

    print(
        json.dumps(results)
    )