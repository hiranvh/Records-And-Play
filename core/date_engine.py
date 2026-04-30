"""
core.date_engine
----------------
Centralized synthetic date timeline generation for profile and replay flows.

Goals
-----
* Keep chronology coherent: DOB < Hire < Effective < Enrollment.
* Keep generated ages realistic for workforce data.
* Avoid extreme historical DOB values unless explicitly provided.
* Support scenario-aware defaults for benefits enrollment vs new employee.
"""

from __future__ import annotations

import random
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

_DOB_FLOOR = date(1951, 1, 1)

_DATE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "dob_date": (
        "dob_date",
        "dob",
        "birth_date",
        "birthdate",
        "date_of_birth",
        "dateofbirth",
    ),
    "hire_date": (
        "hire_date",
        "date_of_hire",
        "dateofhire",
        "employment_date",
        "employmentdate",
        "start_date",
        "startdate",
    ),
    "effective_date": (
        "effective_date",
        "effectivedate",
        "coverage_effective",
        "coverageeffectivedate",
    ),
    "enrollment_date": (
        "enrollment_date",
        "enrollmentdate",
        "coverage_start_date",
        "coveragestartdate",
        "election_date",
        "electiondate",
    ),
    "retirement_date": (
        "retirement_date",
        "retirementdate",
        "retiredate",
        "terminationdate",
        "termdate",
        "end_date",
        "enddate",
    ),
}


def _norm(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(raw or "").lower())


def parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value

    raw = str(value or "").strip()
    if not raw:
        return None

    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def format_date(value: date) -> str:
    return value.strftime("%m/%d/%Y")


def add_years(base: date, years: int) -> date:
    try:
        return base.replace(year=base.year + years)
    except ValueError:
        # Handle leap day rollover safely.
        return base.replace(month=2, day=28, year=base.year + years)


def first_day_next_month(base: date) -> date:
    year = base.year + (1 if base.month == 12 else 0)
    month = 1 if base.month == 12 else base.month + 1
    return date(year, month, 1)


def infer_date_scenario(
    hint_text: str = "",
    hint_fields: Optional[Iterable[str]] = None,
) -> str:
    fields_blob = " ".join(str(v or "") for v in (hint_fields or []))
    blob = _norm(f"{hint_text} {fields_blob}")

    new_employee_tokens = (
        "newemployee",
        "newemployees",
        "newhire",
        "employeeadministration",
        "employeevm",
        "employees/",
        "/new/employees",
    )
    enrollment_tokens = (
        "enrollment",
        "activateandenroll",
        "benefit",
        "coverage",
        "planselection",
        "election",
        "openenrollment",
    )

    if any(token in blob for token in new_employee_tokens):
        return "new_employee"
    if any(token in blob for token in enrollment_tokens):
        return "benefits_enrollment"
    return "general"


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - date(year, month, 1)).days


def _random_date(rand: random.Random, start: date, end: date) -> date:
    if end < start:
        return start
    return date.fromordinal(rand.randint(start.toordinal(), end.toordinal()))


def _lookup_override_date(overrides: Dict[str, Any], canonical: str) -> Optional[date]:
    for alias in _DATE_ALIASES.get(canonical, ()):
        value = overrides.get(_norm(alias))
        parsed = parse_date(value)
        if parsed is not None:
            return parsed
    return None


def _generate_dob(rand: random.Random, scenario: str, today: date) -> date:
    if scenario == "new_employee":
        year_min = max(1960, today.year - 45)
        year_max = min(today.year - 21, 2004)
    elif scenario == "benefits_enrollment":
        year_min = max(1960, today.year - 65)
        year_max = min(today.year - 25, 2000)
    else:
        year_min = max(1960, today.year - 65)
        year_max = min(today.year - 25, 2000)

    if year_max < year_min:
        year_min = max(1951, today.year - 70)
        year_max = max(year_min, today.year - 18)

    year = rand.randint(year_min, year_max)
    month = rand.randint(1, 12)
    day = rand.randint(1, _days_in_month(year, month))
    return date(year, month, day)


def _generate_hire_date(rand: random.Random, dob: date, scenario: str, today: date) -> date:
    minimum = add_years(dob, 18) + timedelta(days=1)
    preferred = add_years(dob, 21)

    if scenario == "new_employee":
        start = max(minimum, today - timedelta(days=365 * 2))
    else:
        start = max(minimum, preferred)

    end = today
    if start > end:
        start = minimum
        end = max(start, today)

    return _random_date(rand, start, end)


def _generate_effective_date(rand: random.Random, hire: date, scenario: str, today: date) -> date:
    # Global business policy: Effective Date is Jan 1 of the current run year.
    return date(today.year, 1, 1)


def _generate_enrollment_date(
    rand: random.Random,
    effective: date,
    scenario: str,
    today: date,
) -> date:
    minimum = effective + timedelta(days=1)

    if scenario == "benefits_enrollment":
        end = min(effective + timedelta(days=45), today + timedelta(days=270))
    else:
        end = effective + timedelta(days=30)

    return _random_date(rand, minimum, max(minimum, end))


def _generate_retirement_date(rand: random.Random, dob: date, hire: date) -> date:
    target = add_years(dob, rand.randint(58, 70))
    minimum = max(add_years(dob, 55), hire + timedelta(days=365))
    if target < minimum:
        target = minimum + timedelta(days=rand.randint(30, 365 * 5))
    return target


def build_realistic_timeline(
    rand: random.Random,
    overrides: Optional[Dict[str, Any]] = None,
    scenario: str = "general",
    today: Optional[date] = None,
) -> Tuple[Dict[str, date], List[str]]:
    """Build a chronology-safe timeline and return date objects plus corrections."""
    now = today or date.today()
    normalized = {
        _norm(str(key)): value
        for key, value in dict(overrides or {}).items()
        if value not in (None, "")
    }

    corrections: List[str] = []

    explicit_dob = _lookup_override_date(normalized, "dob_date")
    explicit_hire = _lookup_override_date(normalized, "hire_date")
    explicit_effective = _lookup_override_date(normalized, "effective_date")
    explicit_enrollment = _lookup_override_date(normalized, "enrollment_date")
    explicit_retirement = _lookup_override_date(normalized, "retirement_date")

    dob = explicit_dob or _generate_dob(rand, scenario, now)
    hire = explicit_hire or _generate_hire_date(rand, dob, scenario, now)
    effective = explicit_effective or _generate_effective_date(rand, hire, scenario, now)
    enrollment = explicit_enrollment or _generate_enrollment_date(rand, effective, scenario, now)
    retirement = explicit_retirement or _generate_retirement_date(rand, dob, hire)

    min_dob = _DOB_FLOOR
    max_dob = add_years(now, -18)
    if dob < min_dob:
        dob = min_dob
        corrections.append("DOB adjusted to avoid extreme historical values")
    if dob > max_dob:
        dob = max_dob
        corrections.append("DOB adjusted to maintain minimum age 18")

    min_hire = add_years(dob, 18) + timedelta(days=1)
    preferred_hire = add_years(dob, 21)
    if hire < min_hire:
        hire = min_hire
        corrections.append("Hire Date adjusted to stay after DOB + 18 years")
    if hire > now:
        hire = now
        corrections.append("Hire Date adjusted to not exceed today")
    if explicit_hire is None and hire < preferred_hire <= now:
        # Keep generated data mostly in a realistic 21+ employment range.
        hire = _random_date(rand, preferred_hire, now)
        corrections.append("Hire Date adjusted into preferred 21+ employment range")

    policy_effective = date(now.year, 1, 1)
    if effective != policy_effective:
        effective = policy_effective
        corrections.append("Effective Date aligned to Jan 1 of current year")

    if hire >= effective:
        safe_hire = effective - timedelta(days=1)
        min_hire = add_years(dob, 18) + timedelta(days=1)

        if safe_hire < min_hire:
            dob_latest = add_years(safe_hire, -18) - timedelta(days=1)
            if dob > dob_latest:
                dob = max(_DOB_FLOOR, dob_latest)
                corrections.append("DOB adjusted so Hire Date can precede Effective Date policy")
            min_hire = add_years(dob, 18) + timedelta(days=1)

        hire = min(safe_hire, now)
        if hire < min_hire:
            hire = min_hire
        if hire >= effective:
            hire = effective - timedelta(days=1)
        corrections.append("Hire Date adjusted to stay before Effective Date policy")

    min_enrollment = effective + timedelta(days=1)
    if enrollment < min_enrollment:
        enrollment = min_enrollment
        corrections.append("Enrollment Date adjusted to be after Effective Date")
    max_enrollment = effective + timedelta(days=365)
    if enrollment > max_enrollment:
        enrollment = max_enrollment
        corrections.append("Enrollment Date adjusted to remain near Effective Date")

    min_retirement = max(add_years(dob, 55), hire + timedelta(days=365))
    if retirement < min_retirement:
        retirement = min_retirement
        corrections.append("Retirement Date adjusted to preserve 55+ retirement age")

    window_start = effective
    window_end = max(enrollment, effective + timedelta(days=30))

    timeline = {
        "dob_date": dob,
        "hire_date": hire,
        "effective_date": effective,
        "enrollment_date": enrollment,
        "retirement_date": retirement,
        "enrollment_window_start": window_start,
        "enrollment_window_end": window_end,
    }
    return timeline, corrections


def timeline_to_profile_fields(timeline: Dict[str, date]) -> Dict[str, str]:
    """Expand timeline dates into profile-compatible text/date-component fields."""
    out: Dict[str, str] = {}

    def _add_parts(prefix: str, key: str) -> None:
        value = timeline[key]
        out[key] = format_date(value)
        out[f"{prefix}_year"] = str(value.year)
        out[f"{prefix}_month"] = f"{value.month:02d}"
        out[f"{prefix}_month_index"] = str(value.month - 1)
        out[f"{prefix}_day"] = str(value.day)

    _add_parts("dob", "dob_date")
    _add_parts("hire", "hire_date")
    _add_parts("effective", "effective_date")
    _add_parts("enrollment", "enrollment_date")

    out["retirement_date"] = format_date(timeline["retirement_date"])
    out["enrollment_window_start"] = format_date(timeline["enrollment_window_start"])
    out["enrollment_window_end"] = format_date(timeline["enrollment_window_end"])
    return out


__all__ = [
    "add_years",
    "build_realistic_timeline",
    "first_day_next_month",
    "format_date",
    "infer_date_scenario",
    "parse_date",
    "timeline_to_profile_fields",
]
