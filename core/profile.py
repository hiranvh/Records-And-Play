"""
core.profile
~~~~~~~~~~~~
Synthetic profile generation plus field identification and value inference.

All data created here is synthetic and intended solely for automated form
testing. Nothing is used to impersonate a real person.
"""

import random
import re
from datetime import date
from typing import Dict, Optional

from core.constants import DEFAULT_VALID_ZIP, ZIP_LOCATION_DATA
from core.date_engine import (
    build_realistic_timeline,
    infer_date_scenario,
    timeline_to_profile_fields,
)
from core.utils import clean_text, normalize_text

try:
    from faker import Faker
    fake = Faker()
except ImportError:
    fake = None


# ─────────────────────────────────────────────────────────────────────────────
# Low-level generators
# ─────────────────────────────────────────────────────────────────────────────

def generate_ssn() -> str:
    """Return a syntactically valid fake SSN in XXX-XX-XXXX format."""

    def _valid(digits: str) -> bool:
        if len(digits) != 9 or not digits.isdigit():
            return False
        area, group, serial = int(digits[:3]), int(digits[3:5]), int(digits[5:])
        if area in (0, 666) or area >= 900:
            return False
        if area == 987 and group == 65 and digits[5:8] == "432":
            return False
        return group != 0 and serial != 0

    if fake is not None:
        try:
            raw = "".join(ch for ch in fake.ssn() if ch.isdigit())
            if _valid(raw):
                return f"{raw[:3]}-{raw[3:5]}-{raw[5:]}"
        except Exception:
            pass

    # Rule-based fallback
    while True:
        area = random.randint(1, 899)
        if area == 666:
            continue
        group = random.randint(1, 99)
        serial = random.randint(1, 9999)
        digits = f"{area:03d}{group:02d}{serial:04d}"
        if _valid(digits):
            return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def generate_phone() -> str:
    """Return a fake 10-digit US phone number with a Maryland area code."""
    area_code = random.choice(["301", "240", "227", "410", "443", "667"])
    return f"({area_code}) {random.randint(200, 999)}-{random.randint(1000, 9999)}"


def normalize_zip_extension(value: str = "", fallback_zip: str = "") -> str:
    """Return a 4-digit ZIP+4 extension, padding short inputs when needed."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) >= 4:
        return digits[:4]
    if digits:
        return digits.zfill(4)

    fallback_digits = "".join(ch for ch in str(fallback_zip or "") if ch.isdigit())
    if len(fallback_digits) >= 9:
        return fallback_digits[5:9]

    return f"{random.randint(0, 9999):04d}"


# ─────────────────────────────────────────────────────────────────────────────
# Date helpers
# ─────────────────────────────────────────────────────────────────────────────


def _calculate_age(dob: date, on: Optional[date] = None) -> int:
    on = on or date.today()
    years = on.year - dob.year
    if (on.month, on.day) < (dob.month, dob.day):
        years -= 1
    return max(0, years)


# ─────────────────────────────────────────────────────────────────────────────
# Rule parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _canonical_rule_name(value: str) -> str:
    normalized = normalize_text(value)
    aliases: dict[str, list[str]] = {
        "first_name": ["firstname", "givenname"],
        "last_name": ["lastname", "surname", "familyname"],
        "gender": ["gender", "sex"],
        "marital": ["maritalstatus", "marital"],
        "dob": ["dob", "dateofbirth", "birthdate"],
        "effective_date": ["effectivedate", "effective", "coverageeffectivedate"],
        "enrollment_date": ["enrollmentdate", "electiondate", "coveragestartdate"],
        "ssn": ["ssn", "socialsecurity", "socialsecuritynumber"],
        "tobacco": [
            "tobaccouseinthelast6months",
            "tobaccouse",
            "tobacoind",
            "tobacco",
            "smoker",
            "smoking",
        ],
        "phone": ["cellphone", "mobilephone", "mobile", "cell", "phone", "altphone2"],
        "address1": [
            "address1",
            "addressline1",
            "streetaddress",
            "street",
            "homeaddressaddress1",
        ],
        "zip_extension": ["zipextension", "zipcodeextension", "postalcodeextension", "postcodeextension"],
        "zip": ["zip", "zipcode", "postalcode", "postcode"],
        "county": ["county"],
        "city": ["city"],
        "state": ["state", "province"],
        "billing_location": ["billinglocation", "billing", "subgroup", "subgroupid"],
        "employee_class": [
            "employeeclass",
            "classid",
            "employeetype",
            "class",
        ],
        "hire_date": [
            "dateofhire",
            "hiredate",
            "datehired",
            "employmentdate",
            "startdate",
        ],
        "retirement_date": ["retirementdate", "retiredate", "terminationdate", "termdate", "enddate"],
    }
    for canonical, tokens in aliases.items():
        if any(t == normalized or t in normalized for t in tokens):
            return canonical
    return ""


def _parse_fixed_rules(rules_text: str) -> dict[str, str]:
    rules: dict[str, str] = {}
    for raw_line in str(rules_text or "").splitlines():
        line = raw_line.strip().lstrip("-*0123456789. ").strip()
        if not line:
            continue
        if "->" in line:
            raw_key, raw_val = line.split("->", 1)
        elif ":" in line:
            raw_key, raw_val = line.split(":", 1)
        else:
            raw_key, raw_val = line, ""
        canonical = _canonical_rule_name(raw_key)
        if canonical:
            rules[canonical] = clean_text(raw_val)
    return rules


def _lookup_override(overrides: dict, aliases: list[str]) -> str:
    norm_aliases = [normalize_text(a) for a in aliases]
    for key, val in (overrides or {}).items():
        nk = normalize_text(key)
        if any(a and (a == nk or a in nk) for a in norm_aliases):
            cleaned = clean_text(val)
            if cleaned:
                return cleaned
    return ""


def _coerce_tobacco(value: str) -> str:
    n = normalize_text(value)
    if n in {"true", "yes", "y", "1"}:
        return "true"
    if n in {"false", "no", "n", "0"}:
        return "false"
    return ""


def _lookup_zip(zip_code: str) -> dict[str, str]:
    raw_zip = str(zip_code or "").strip()
    digits = "".join(ch for ch in raw_zip if ch.isdigit())
    
    zip5 = digits[:5]
    ext = digits[5:9] if len(digits) >= 9 else ""
    
    if len(zip5) != 5:
        default_digits = "".join(ch for ch in DEFAULT_VALID_ZIP if ch.isdigit())
        zip5 = default_digits[:5]
        ext = default_digits[5:9] if len(default_digits) >= 9 else ""

    ext = normalize_zip_extension(ext, raw_zip or zip5)
        
    location = ZIP_LOCATION_DATA.get(zip5, {})
    return {
        "zip": zip5,
        "zip_extension": ext,
        "county": location.get("county", ""),
        "city": location.get("city", ""),
        "state": location.get("state", ""),
        "billing_location": location.get("billing_location", ""),
        "employee_class": location.get("employee_class", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main profile generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_profile(
    override_data_normalized: Optional[dict] = None,
    fixed_rules_text: str = "",
) -> dict:
    """Generate one correlated fake person suitable for a full automation run.

    Parameters
    ----------
    override_data_normalized:
        Lowercase key → value overrides (e.g. ``{"ssn": "123-45-6789"}``).
    fixed_rules_text:
        Free-text rules extracted from the workflow (e.g. "DOB -> 01/01/1990").

    Returns
    -------
    dict
        All profile fields required by the form-filling engine.
    """
    rules = _parse_fixed_rules(fixed_rules_text)
    overrides = override_data_normalized or {}

    # ── ZIP / location ─────────────────────────────────────────────────────
    zip_val = _lookup_override(overrides, ["zip", "zipcode", "postal code", "postcode"])
    zip_profile = _lookup_zip(zip_val or rules.get("zip", "") or DEFAULT_VALID_ZIP)

    # ── Gender ─────────────────────────────────────────────────────────────
    gender = _lookup_override(overrides, ["gender", "sex"]) or clean_text(
        rules.get("gender", "")
    )
    if gender.lower() not in {"male", "female"}:
        gender = random.choice(["Male", "Female"])
    else:
        gender = gender.title()

    # ── Name ───────────────────────────────────────────────────────────────
    first = (
        _lookup_override(overrides, ["first name", "firstname", "given name"])
        or (fake.first_name_male() if gender == "Male" and fake else None)
        or (fake.first_name_female() if fake else "Jane")
        or ("John" if gender == "Male" else "Jane")
    )
    last = (
        _lookup_override(overrides, ["last name", "lastname", "surname"])
        or (fake.last_name() if fake else "Doe")
    )
    middle = fake.first_name() if fake else "A"

    # ── Address ────────────────────────────────────────────────────────────
    addr1 = (
        _lookup_override(overrides, ["address1", "address 1", "street address", "address"])
        or (fake.street_address() if fake else f"{random.randint(100, 9999)} Main St")
    )
    addr2 = fake.secondary_address() if fake else "Apt 1"

    # ── Timeline (DOB/Hire/Effective/Enrollment/Retirement) ───────────────
    date_overrides: Dict[str, str] = {
        "dob_date": _lookup_override(overrides, ["dob", "dob_date", "date of birth", "birth date"]) or rules.get("dob", ""),
        "hire_date": _lookup_override(overrides, ["date of hire", "hire date", "employment date", "start date"]) or rules.get("hire_date", ""),
        "effective_date": _lookup_override(overrides, ["effective date", "effective", "coverage effective date"]) or rules.get("effective_date", ""),
        "enrollment_date": _lookup_override(overrides, ["enrollment date", "coverage start date", "election date"]) or rules.get("enrollment_date", ""),
        "retirement_date": _lookup_override(overrides, ["retirement date", "retire date", "termination date", "term date"]) or rules.get("retirement_date", ""),
    }
    scenario = infer_date_scenario(
        hint_text=(
            f"{fixed_rules_text} "
            f"{' '.join(str(k) for k in overrides.keys())} "
            f"{' '.join(str(v) for v in overrides.values() if isinstance(v, (str, int, float)))}"
        ),
        hint_fields=list(overrides.keys()),
    )
    timeline, _ = build_realistic_timeline(
        rand=random,
        overrides=date_overrides,
        scenario=scenario,
    )
    timeline_fields = timeline_to_profile_fields(timeline)
    dob = timeline["dob_date"]
    hire = timeline["hire_date"]
    effective = timeline["effective_date"]

    # ── Other fields ───────────────────────────────────────────────────────
    ssn = (
        _lookup_override(overrides, ["ssn", "social security", "social security number"])
        or generate_ssn()
    )
    phone = (
        _lookup_override(overrides, ["cell phone", "phone", "mobile", "cell"])
        or generate_phone()
    )
    marital = (
        _lookup_override(overrides, ["marital status", "marital"])
        or clean_text(rules.get("marital", ""))
        or "Single"
    ).strip().title() or "Single"

    tobacco_raw = (
        _lookup_override(overrides, ["tobacco", "smoker", "smoking", "tobaco"])
        or rules.get("tobacco", "")
        or "false"
    )
    tobacco = _coerce_tobacco(tobacco_raw) or "false"

    age = _calculate_age(dob)

    if marital in {"Married", "Domestic Partner"}:
        sponsor_rel, dep_rel = "Spouse", "Spouse"
    else:
        sponsor_rel, dep_rel = "Subscriber", "Child"

    email = (
        f"{first}.{last}{random.randint(10, 999)}@example.org"
        .lower().replace(" ", "")
    )
    job_title = fake.job() if fake else "Administrative Specialist"
    bargaining = random.choice(["Administrative", "Classified", "Instructional", "Support"])
    prefix = "Mr." if gender == "Male" else "Ms."
    suffix = random.choice(["", "II", "III"])

    county = _lookup_override(overrides, ["county"]) or zip_profile.get("county") or "PRINCE GEORGE'S"
    city = _lookup_override(overrides, ["city"]) or zip_profile.get("city") or "BELTSVILLE"
    state = _lookup_override(overrides, ["state", "province"]) or zip_profile.get("state") or "Maryland"
    billing = (
        _lookup_override(overrides, ["billing location", "billing", "subgroup", "subgroup id"])
        or zip_profile.get("billing_location")
        or ""
    )
    emp_class = (
        _lookup_override(overrides, ["employee class", "class id", "employee type"])
        or zip_profile.get("employee_class")
        or ""
    )

    return {
        "gender": gender,
        "first_name": first,
        "middle_name": middle,
        "last_name": last,
        "full_name": f"{first} {last}".strip(),
        "email": email,
        "ssn": ssn,
        "phone": phone,
        "work_phone": phone,
        "cell_phone": phone,
        "address1": addr1,
        "address2": addr2,
        "zip": zip_profile["zip"],
        "zip_extension": zip_profile["zip_extension"],
        "county": county,
        "city": city,
        "state": state,
        "age": str(age),
        "prefix": prefix,
        "suffix": suffix,
        "billing_location": billing,
        "employee_class": emp_class,
        "marital": marital,
        "job_title": job_title,
        "bargaining_unit": bargaining,
        "sponsor_relation": sponsor_rel,
        "dependent_relation": dep_rel,
        "relation": dep_rel,
        "tobacco": tobacco,
        "tobacco_label": "Yes" if tobacco == "true" else "No",
        "disabled": "false",
        "disabled_label": "No",
        "medicare_eligible": "false",
        "medicare_eligible_label": "No",
        "sponsor_ssn": generate_ssn(),
        **timeline_fields,
        "employee_id": str(random.randint(10000, 99999)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Field identification
# ─────────────────────────────────────────────────────────────────────────────

def identify_profile_field(
    label: str = "",
    name: str = "",
    id_attr: str = "",
    text: str = "",
    selector: str = "",
) -> str:
    """Map arbitrary DOM field attributes to a profile field key."""
    c = normalize_text(" ".join([label, name, id_attr, text, selector]))

    if "dateofbirth" in c or "birthdate" in c or ("dob" in c and "dateofhire" not in c):
        return "dob_date"
    if any(t in c for t in ("dateofhire", "hiredate", "datehired", "employmentdate", "startdate")):
        return "hire_date"
    if "effectivedate" in c or "coverageeffectivedate" in c:
        return "effective_date"
    if "enrollmentdate" in c or "coveragestartdate" in c or "electiondate" in c:
        return "enrollment_date"
    if "firstname" in c or "givenname" in c:
        return "first_name"
    if "middlename" in c or "middleinitial" in c or "middle" in c:
        return "middle_name"
    if "lastname" in c or "surname" in c or "familyname" in c:
        return "last_name"
    if "email" in c or "emailaddress" in c:
        return "email"
    # Detect Zip Extension first
    if any(t in c for t in ("zipextension", "postalcodeextension", "zipcodeextension", "zip_extension")) or ("extension" in c and "zip" in c):
        return "zip_extension"
    # ZIP must be checked before SSN to avoid misidentifying a zip field whose
    # selector/container path happens to contain the letters "ssn".
    if any(t in c for t in ("zipcode", "postalcode", "postcode", "zip")):
        return "zip"
    if "socialsecurity" in c or "ssn" in c:
        return "ssn"
    if any(t in c for t in ("cellphone", "mobilephone", "altphone", "phone", "mobile", "cell")):
        return "phone"
    if "workphone" in c or "officephone" in c:
        return "work_phone"
    if any(t in c for t in ("address1", "addressline1", "streetaddress", "homeaddressaddress1")):
        return "address1"
    if any(t in c for t in ("address2", "addressline2", "secondaryaddress")):
        return "address2"
    # NOTE: zip is already checked above (before ssn) — no duplicate needed here.
    if "county" in c:
        return "county"
    if "city" in c:
        return "city"
    if "state" in c or "province" in c:
        return "state"
    if "maritalstatus" in c or ("marital" in c and "status" in c):
        return "marital"
    if "gender" in c or c.endswith("sex"):
        return "gender"
    # jobtitle must be checked BEFORE the generic "title" prefix check because
    # "title" is a substring of "jobtitle" and would falsely return "prefix".
    if "jobtitle" in c or "occupation" in c or "position" in c:
        return "job_title"
    if "prefix" in c or "title" in c:
        return "prefix"
    if "suffix" in c:
        return "suffix"
    if "age" in c:
        return "age"
    if any(t in c for t in ("tobacco", "tobaco", "smoker", "smoking")):
        return "tobacco"
    if any(t in c for t in ("disabled", "disability", "handicap")):
        return "disabled"
    if "medicare" in c:
        return "medicare_eligible"
    if "sponsorssn" in c or ("sponsor" in c and "ssn" in c):
        return "sponsor_ssn"
    if "retirementdate" in c or "retiredate" in c:
        return "retirement_date"
    if "billinglocation" in c or "subgroupid" in c:
        return "billing_location"
    if "employeeclass" in c or "classid" in c:
        return "employee_class"
    if "bargainingunit" in c or "union" in c:
        return "bargaining_unit"
    if any(t in c for t in ("employeenumber", "employeeid", "empnumber", "empid", "staffid")):
        return "employee_id"
    if "sponsorrelation" in c:
        return "sponsor_relation"
    if "dependentrelation" in c:
        return "dependent_relation"
    if "relation" in c or "relationship" in c:
        return "relation"

    return ""


def get_profile_value(
    field: str,
    profile: dict,
    step_type: str = "input",
    tag_name: str = "",
) -> Optional[str]:
    """Retrieve the profile value for *field*, applying any tag-specific coercions."""
    if not field:
        return None
    val = profile.get(field)
    if val in (None, ""):
        return None
    if field == "zip_extension":
        return normalize_zip_extension(val, profile.get("zip") or "")
    if field == "phone":
        return profile.get("cell_phone") or val
    if field == "tobacco":
        if step_type == "select" or tag_name in {"label", "li", "span", "a", "div"}:
            return profile.get("tobacco_label") or ("Yes" if str(val).lower() == "true" else "No")
    if field == "disabled":
        if step_type == "select" or tag_name in {"label", "li", "span", "a", "div"}:
            return profile.get("disabled_label") or ("Yes" if str(val).lower() == "true" else "No")
    if field == "medicare_eligible":
        if step_type == "select" or tag_name in {"label", "li", "span", "a", "div"}:
            return profile.get("medicare_eligible_label") or ("Yes" if str(val).lower() == "true" else "No")
    return str(val)


def get_date_context(profile_field: str) -> Optional[str]:
    """Return a datepicker context key (dob/hire/effective/enrollment)."""
    return {
        "dob_date": "dob",
        "hire_date": "hire",
        "effective_date": "effective",
        "enrollment_date": "enrollment",
    }.get(profile_field)


def get_datepicker_value(
    step: dict, profile: dict, calendar_context: str
) -> Optional[str]:
    if calendar_context not in {"dob", "hire", "effective", "enrollment"}:
        return None
    sel = step.get("selector", "")
    if step.get("type") == "select":
        if "select:nth-of-type(2)" in sel:
            return profile.get(f"{calendar_context}_year")
        if "select:nth-of-type(1)" in sel:
            return profile.get(f"{calendar_context}_month_index")
    if step.get("type") in {"click", "click_link"}:
        return profile.get(f"{calendar_context}_day")
    return None


def step_target_blob(step: dict) -> str:
    return normalize_text(
        " ".join(
            str(step.get(k, ""))
            for k in ("id", "name", "label", "selector", "text", "value")
        )
    )


def is_date_related_step(step: dict) -> bool:
    blob = " ".join(
        str(step.get(k, "")).lower()
        for k in ("label", "name", "id", "text", "selector", "value")
    )
    return any(
        t in blob
        for t in ("dob", "dateofbirth", "birth", "date", "datepicker", "ui-datepicker", "hire")
    )


def get_replay_target_key(step: dict, profile_field: str = "") -> Optional[tuple]:
    if step.get("type") not in {"input", "select"}:
        return None
    sel = clean_text(step.get("selector", ""))
    if "ui-datepicker-div" in sel.lower():
        return None
    parts = tuple(
        normalize_text(p)
        for p in (
            step.get("type", ""),
            step.get("tag", ""),
            step.get("id", ""),
            step.get("name", ""),
            sel,
            profile_field or "",
        )
    )
    if any(parts[2:5]):
        return parts
    return None


def should_upgrade_to_input(
    step: dict,
    tag_name: str,
    input_type: str,
    id_attr: str,
    name: str,
    label: str = "",
    placeholder: str = "",
) -> bool:
    if step.get("type") not in {"click", "click_link"}:
        return False
    if tag_name not in {"input", "textarea"}:
        return False
    if input_type in {"checkbox", "radio", "button", "submit", "file", "hidden"}:
        return False
    combined = f"{id_attr.lower()} {name.lower()}"
    if any(t in combined for t in ("btn", "submit", "login", "cancel", "sameas", "tobaco")):
        return False
    date_blob = f"{id_attr.lower()} {name.lower()} {label.lower()} {placeholder.lower()}"
    if any(t in date_blob for t in ("dob", "birth", "hire", "date", "calendar", "datepicker")):
        return False
    return True


def _expand_field_names(field: str) -> set[str]:
    expanded: set[str] = {field}
    if field == "dob":
        expanded.update({"dob_date", "dob_year", "dob_month", "dob_month_index", "dob_day"})
    elif field == "hire_date":
        expanded.update(
            {"hire_date", "hire_year", "hire_month", "hire_month_index", "hire_day"}
        )
    elif field == "effective_date":
        expanded.update(
            {
                "effective_date",
                "effective_year",
                "effective_month",
                "effective_month_index",
                "effective_day",
            }
        )
    elif field == "enrollment_date":
        expanded.update(
            {
                "enrollment_date",
                "enrollment_year",
                "enrollment_month",
                "enrollment_month_index",
                "enrollment_day",
            }
        )
    elif field == "phone":
        expanded.update({"phone", "cell_phone"})
    return expanded


def get_requested_fields(
    override_data_normalized: dict, fixed_rules_text: str
) -> set[str]:
    """Compute the set of profile fields explicitly requested by the user."""
    requested: set[str] = set()
    for field in _parse_fixed_rules(fixed_rules_text):
        requested.update(_expand_field_names(field))
    for key in (override_data_normalized or {}):
        canonical = _canonical_rule_name(key)
        if canonical:
            requested.update(_expand_field_names(canonical))
    return requested


def is_generated_data_field(
    label: str = "",
    name: str = "",
    id_attr: str = "",
    text: str = "",
    selector: str = "",
) -> bool:
    if identify_profile_field(label, name, id_attr, text, selector):
        return True
    c = normalize_text(" ".join([label, name, id_attr, text, selector]))
    return any(
        t in c
        for t in (
            "email",
            "middlename",
            "middleinitial",
            "address2",
            "secondaryaddress",
            "country",
            "age",
            "company",
            "employer",
            "organisation",
            "organization",
            "salary",
            "income",
            "amount",
            "total",
        )
    )


def infer_field_value(
    label: str,
    name: str,
    id_attr: str,
    original_value: str = "",
    execution_profile: Optional[dict] = None,
) -> str:
    """Return a plausible value for any form field using profile → Faker → heuristics."""
    field = identify_profile_field(label, name, id_attr)
    if execution_profile and field:
        v = get_profile_value(field, execution_profile)
        if v is not None:
            # If the label says 'initial' (e.g. "Middle Initial"), truncate to first character
            if field == "middle_name" and "initial" in normalize_text(f"{label} {name} {id_attr}"):
                return v[0] if v else v
            return v

    c = normalize_text(f"{label} {name} {id_attr}")
    combined = f"{label.lower()} {name.lower()} {id_attr.lower()}"

    # Name
    if ("first" in combined and "name" in combined) or c.startswith("firstname"):
        return fake.first_name() if fake else "John"
    if "middle" in combined and ("name" in combined or "initial" in combined):
        # If label says 'initial', return only the first letter
        full_middle = fake.first_name() if fake else "A"
        if "initial" in combined:
            return full_middle[0]
        return full_middle
    if ("last" in combined and "name" in combined) or c.startswith("lastname"):
        return fake.last_name() if fake else "Doe"
    if "fullname" in c or ("full" in combined and "name" in combined):
        return fake.name() if fake else "John Doe"

    # Contact
    if "email" in c:
        try:
            return fake.unique.email()
        except Exception:
            return f"user{random.randint(1000, 9999)}@example.com"
    if any(t in c for t in ("phone", "mobile", "cell", "altphone", "workphone")):
        return generate_phone()
    if "fax" in c:
        return generate_phone()

    # Identity
    if "ssn" in c or "socialsecurity" in c or "taxid" in c:
        return generate_ssn()
    if "ein" in c:
        return f"{random.randint(10, 99)}-{random.randint(1000000, 9999999)}"

    # Address
    if ("address" in c or "addr" in c) and "email" not in c:
        if any(t in c for t in ("2", "line2", "secondary", "suite", "apt")):
            return fake.secondary_address() if fake else "Apt 1"
        return fake.street_address() if fake else "123 Main St"
    if "street" in c:
        return fake.street_address() if fake else "123 Main St"
    if "city" in c:
        return fake.city() if fake else "Springfield"
    if "state" in c or "province" in c:
        return fake.state_abbr() if fake else "CA"
    if "country" in c:
        return "USA"
    if any(t in c for t in ("zipextension", "postalcodeextension", "zipcodeextension", "zip_extension")) or ("extension" in c and "zip" in c):
        return normalize_zip_extension("", DEFAULT_VALID_ZIP)
    if any(t in c for t in ("zip", "postal", "postcode")):
        default_digits = "".join(ch for ch in DEFAULT_VALID_ZIP if ch.isdigit())
        return default_digits[:5]
    if "county" in c:
        return fake.city() if fake else "Montgomery"

    # Dates
    timeline_cache: dict = {}

    def _timeline_field(key: str, fallback: str) -> str:
        if not timeline_cache:
            timeline, _ = build_realistic_timeline(
                rand=random,
                overrides=execution_profile or {},
                scenario=infer_date_scenario(
                    hint_text=f"{label} {name} {id_attr}",
                    hint_fields=list((execution_profile or {}).keys()),
                ),
            )
            timeline_cache.update(timeline_to_profile_fields(timeline))
        return str(timeline_cache.get(key) or fallback)

    if "dob" in c or "dateofbirth" in c or "birthdate" in c:
        return _timeline_field("dob_date", "01/15/1980")
    if any(t in c for t in ("hiredate", "dateofhire", "datehired", "employmentdate", "startdate")):
        return _timeline_field("hire_date", "01/01/2010")
    if "retirementdate" in c or "retiredate" in c:
        return _timeline_field("retirement_date", "01/01/2035")
    if "effectivedate" in c or "coveragedate" in c:
        return _timeline_field("effective_date", "01/01/2026")
    if "enrollmentdate" in c or "coveragestartdate" in c or "electiondate" in c:
        return _timeline_field("enrollment_date", "01/15/2026")
    if "date" in c:
        return _timeline_field("effective_date", "01/01/2026")

    # HR
    if any(t in c for t in ("jobtitle", "occupation", "position", "title")):
        return random.choice(["Analyst", "Coordinator", "Specialist", "Manager", "Associate"])
    if any(t in c for t in ("department", "dept", "division")):
        return random.choice(["Human Resources", "Finance", "Operations", "IT", "Marketing"])
    if any(t in c for t in ("bargainingunit", "union", "bargaining")):
        return random.choice(["Instructional", "Non-Instructional", "Administrative", "Support"])
    if "employeeclass" in c or "empclass" in c or "classid" in c or "employeetype" in c:
        return random.choice(["Full-Time", "Part-Time", "Hourly", "Salary"])

    # Health
    if any(t in c for t in ("tobacco", "smoker", "smoking")):
        return random.choice(["No", "Yes"])
    if any(t in c for t in ("disabled", "disability", "handicap")):
        return "No"
    if "medicare" in c:
        return "No"
    if "sponsorssn" in c or ("sponsor" in c and "ssn" in c):
        return generate_ssn()
    if "salary" in c or "income" in c or "wage" in c or "amount" in c:
        return str(random.randint(35000, 95000))
    if any(t in c for t in ("employeeid", "employeenumber", "empid", "empnumber", "staffid")):
        return str(random.randint(10000, 99999))
    if "age" in c:
        return str(random.randint(21, 65))
    if "company" in c or "employer" in c or "organisation" in c:
        return fake.company() if fake else "ACME Corp"

    return original_value or ""
