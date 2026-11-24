"""When a flow runs: 'every:6h' (also m and d) or a cron line with five fields, in the container's time zone."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

INTERVAL = re.compile(r"^every:(\d{1,4})([mhd])$")
UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}
UNIT_NAMES = {"m": ("minute", "minutes"), "h": ("hour", "hours"), "d": ("day", "days")}
MIN_SECONDS = 15 * 60
MAX_SECONDS = 30 * 86400
MONTHS = {name: number for number, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
WEEKDAYS = {name: number for number, name in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


class ScheduleError(Exception):
    """The schedule cannot be read; the message is meant for the form."""


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    any_day: bool
    any_weekday: bool

    def day_matches(self, moment: datetime) -> bool:
        if moment.month not in self.months:
            return False
        weekday = (moment.weekday() + 1) % 7
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return weekday in self.weekdays
        if self.any_weekday:
            return moment.day in self.days
        # Like cron: with both restricted, either one is enough.
        return moment.day in self.days or weekday in self.weekdays


def _field(text: str, low: int, high: int, names: dict[str, int] | None, what: str) -> frozenset[int]:
    values: set[int] = set()
    for part in text.lower().split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise ScheduleError(f"Cron: wrong step in the {what} field")
            step = int(step_text)
        if part in ("*", ""):
            start, end = low, high
        elif "-" in part:
            first, last = part.split("-", 1)
            start, end = _number(first, names, what), _number(last, names, what)
        else:
            start = _number(part, names, what)
            end = high if step > 1 else start
        if start < low or end > high or start > end:
            raise ScheduleError(f"Cron: the {what} field allows {low}–{high}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


def _number(text: str, names: dict[str, int] | None, what: str) -> int:
    if names and text in names:
        return names[text]
    if not text.isdigit():
        raise ScheduleError(f"Cron: '{text}' is not valid in the {what} field")
    return int(text)


def parse_cron(text: str) -> Cron:
    parts = text.split()
    if len(parts) != 5:
        raise ScheduleError("Cron needs five fields: minute hour day month weekday")
    minute, hour, day, month, weekday = parts
    return Cron(
        minutes=_field(minute, 0, 59, None, "minute"),
        hours=_field(hour, 0, 23, None, "hour"),
        days=_field(day, 1, 31, None, "day"),
        months=_field(month, 1, 12, MONTHS, "month"),
        weekdays=frozenset(0 if number == 7 else number for number in _field(weekday, 0, 7, WEEKDAYS, "weekday")),
        any_day=day == "*",
        any_weekday=weekday == "*",
    )


def normalise(value: str | None) -> str | None:
    """The stored form of a schedule; None means manual. Raises ScheduleError."""
    text = " ".join(str(value or "").split())
    if not text or text.lower() == "manual":
        return None
    match = INTERVAL.match(text.lower())
    if match:
        seconds = int(match.group(1)) * UNIT_SECONDS[match.group(2)]
        if seconds < MIN_SECONDS:
            raise ScheduleError("Run at most every 15 minutes")
        if seconds > MAX_SECONDS:
            raise ScheduleError("Run at least every 30 days")
        return text.lower()
    if text.lower().startswith("every"):
        raise ScheduleError("Use every:<number><m|h|d>, e.g. every:6h")
    parse_cron(text)
    if next_run(text, datetime.now().timestamp()) is None:
        raise ScheduleError("Cron: this date never comes")
    return text


def describe(value: str | None) -> str:
    if not value:
        return "Manual"
    match = INTERVAL.match(value)
    if match:
        number, unit = int(match.group(1)), match.group(2)
        if number == 1:
            return f"Every {UNIT_NAMES[unit][0]}"
        return f"Every {number} {UNIT_NAMES[unit][1]}"
    try:
        cron = parse_cron(value)
    except ScheduleError:
        return value
    minute, hour, day, month, weekday = value.split()
    if minute.isdigit() and hour.isdigit() and day == "*" and month == "*":
        time_text = f"{int(hour):02d}:{int(minute):02d}"
        if weekday == "*":
            return f"Daily at {time_text}"
        if len(cron.weekdays) == 1:
            return f"{DAY_NAMES[next(iter(cron.weekdays))]}s at {time_text}"
    return f"Cron {value}"


def next_run(value: str | None, after: float) -> int | None:
    """Unix time of the next run after 'after'; None for manual flows."""
    if not value:
        return None
    match = INTERVAL.match(value)
    if match:
        return int(after) + int(match.group(1)) * UNIT_SECONDS[match.group(2)]
    try:
        cron = parse_cron(value)
    except ScheduleError:
        return None
    start = datetime.fromtimestamp(int(after)).replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.replace(hour=0, minute=0)
    hours = sorted(cron.hours)
    minutes = sorted(cron.minutes)
    for _ in range(366 * 5):
        if cron.day_matches(day):
            for hour in hours:
                for minute in minutes:
                    moment = day.replace(hour=hour, minute=minute)
                    if moment >= start:
                        return int(moment.timestamp())
        day += timedelta(days=1)
    return None  # e.g. 31 February
