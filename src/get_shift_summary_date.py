import json
from datetime import datetime, timedelta


def revel_date_field_shift():
    """Returns the date of the last Monday as a string."""

    storename = "leander"
    store_name = storename.upper()

    today_date = datetime.now()
    today_date = datetime(2026, 9, 21, 14, 30, 0)

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
    results = revel_date_field_shift()

    print(
        json.dumps(results)
    )