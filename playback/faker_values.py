"""
playback.faker_values
---------------------
Deterministic synthetic data generation for replay runs.

Highlights
----------
* Preserves coherent employee profile values across all fields.
* Enforces business-safe date relationships (DOB -> Hire Date -> Effective Date).
* Supports optional local Ollama profile generation with automatic fallback.
* Provides metadata for reporting (source, seed, corrections, warnings).
"""
from __future__ import annotations

import json
import os
import random
import re
from datetime import date
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib import parse as urlparse

from faker import Faker

from core.constants import DEFAULT_VALID_ZIP
from core.date_engine import (
    build_realistic_timeline,
    format_date,
    infer_date_scenario,
    parse_date,
    timeline_to_profile_fields,
)

# US zip codes commonly used in healthcare/employer test fixtures.
_US_HEALTHCARE_ZIPS = [
    "20705",  # Beltsville, MD
    "20706",  # Lanham, MD
    "20784",  # Hyattsville, MD
    "20902",  # Silver Spring, MD
    "22003",  # Annandale, VA
    "32202",  # Jacksonville, FL
    "32207",  # Jacksonville, FL
    "33101",  # Miami, FL
    "10001",  # New York, NY
    "30301",  # Atlanta, GA
]

if TYPE_CHECKING:
    from .models import WorkflowStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (raw or "").lower()).strip("_")


def _digits(raw: str) -> str:
    return re.sub(r"\D+", "", str(raw or ""))


def _fmt_date(value: date) -> str:
    return format_date(value)


def _to_date(value: Any) -> Optional[date]:
    return parse_date(value)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return int(default)


def _extract_json_object(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        try:
            parsed = json.loads(fence.group(1))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


# ---------------------------------------------------------------------------
# Used-identity store (kept for non-seeded runs)
# ---------------------------------------------------------------------------

_IDENTITY_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs",
    "used_identities.json",
)
_IDENTITY_STORE_MAX = 10_000


def _load_used_identities() -> list:
    try:
        with open(_IDENTITY_STORE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_used_identities(identities: list) -> None:
    os.makedirs(os.path.dirname(_IDENTITY_STORE_PATH), exist_ok=True)
    pruned = identities[-_IDENTITY_STORE_MAX:]
    with open(_IDENTITY_STORE_PATH, "w", encoding="utf-8") as fh:
        json.dump(pruned, fh, indent=2)


def _identity_key(first: str, last: str, ssn: str, phone: str) -> str:
    return f"{first.lower()}|{last.lower()}|{ssn}|{phone}"


def _safe_phone(fake: Faker) -> str:
    area = str(fake.random_int(min=200, max=989))
    exchange = str(fake.random_int(min=200, max=989))
    line = str(fake.random_int(min=1000, max=9999)).zfill(4)
    return f"({area}) {exchange}-{line}"


def _generate_unique_identity(fake: Faker, max_attempts: int = 50) -> Dict[str, str]:
    used = _load_used_identities()
    used_keys = {entry["key"] for entry in used if "key" in entry}

    for _ in range(max_attempts):
        first = fake.first_name()
        last = fake.last_name()
        ssn = fake.ssn()
        phone = _safe_phone(fake)
        key = _identity_key(first, last, ssn, phone)
        if key not in used_keys:
            used.append({"key": key, "first": first, "last": last, "ssn": ssn, "phone": phone})
            _save_used_identities(used)
            return {"first": first, "last": last, "ssn": ssn, "phone": phone}

    first = fake.first_name()
    last = fake.last_name()
    ssn = fake.ssn()
    phone = _safe_phone(fake)
    key = _identity_key(first, last, ssn, phone)
    used.append({"key": key, "first": first, "last": last, "ssn": ssn, "phone": phone})
    _save_used_identities(used)
    return {"first": first, "last": last, "ssn": ssn, "phone": phone}


# ---------------------------------------------------------------------------
# Pattern table
# ---------------------------------------------------------------------------

_PATTERNS = [
    # Names
    ("firstname|first_name|fname|givenname|given_name|f_name", lambda f: f.__identity__.get("first") or f.first_name()),
    (
        "middlename|middle_name|mname|middleinitial|middle_initial|mid_init",
        lambda f: (f.__identity__.get("middle_name") or "A")[0],
    ),
    ("lastname|last_name|lname|surname|familyname|family_name|l_name", lambda f: f.__identity__.get("last") or f.last_name()),
    (
        "fullname|full_name",
        lambda f: (f"{f.__identity__.get('first') or ''} {f.__identity__.get('last') or ''}".strip() or f.name()),
    ),
    (
        "jobtitle|job_title|positiontitle|position_title|occupation",
        lambda f: f.__identity__.get("job_title") or f.random_element(["Analyst", "Clerk", "Coordinator", "Manager", "Specialist"]),
    ),
    ("suffix", lambda f: f.__identity__.get("suffix") or f.suffix()),
    ("prefix|salutation|title", lambda f: f.__identity__.get("prefix") or f.prefix()),

    # Contact
    ("email", lambda f: f.__identity__.get("email") or f.email()),
    ("phone|cellphone|mobile|mobilephone|cell_phone|telephone|tel", lambda f: f.__identity__.get("phone") or _safe_phone(f)),
    ("fax", lambda f: _safe_phone(f)),

    # Identity
    ("ssn|socialsecurity|social_security|socialsecuritynumber", lambda f: f.__identity__.get("ssn") or f.ssn()),
    (
        "empid|employeeid|employee_id|staffid|staff_id|workerid",
        lambda f: f.__identity__.get("employee_id") or f.numerify("EMP#####"),
    ),
    ("ext1|workext|work_ext|phoneext|phone_ext|extension", lambda f: f.numerify("####")),
    (
        "bargainingunit|bargaining_unit|unioncode|union_code",
        lambda f: f.__identity__.get("bargaining_unit") or f.bothify("UNIT##"),
    ),

    # Address
    ("address1|address_1|streetaddress|addr1|addressline1|street_addr", lambda f: f.__identity__.get("address1") or f.street_address()),
    ("address2|address_2|apt|suite|unit|addressline2", lambda f: f.__identity__.get("address2") or f.secondary_address()),
    ("city", lambda f: f.__identity__.get("city") or f.city()),
    ("county", lambda f: f.__identity__.get("county") or f.city()),
    ("state|province", lambda f: f.__identity__.get("state") or f.state_abbr()),
    (
        "zipext|zip_ext|zip_extension|zipextension|zipcodeextension|zipcode_extension",
        lambda f: f.__identity__.get("zip_extension") or f.numerify("####"),
    ),
    ("zip|zipcode|zip_code|postalcode|postal_code", lambda f: f.__identity__.get("zip") or DEFAULT_VALID_ZIP),
    ("country", lambda f: "US"),

    # Dates
    ("dob|birthdate|birth_date|dateofbirth|date_of_birth", lambda f: f.__identity__.get("dob_date") or f.date_of_birth(minimum_age=25, maximum_age=65).strftime("%m/%d/%Y")),
    ("hiredate|hire_date|dateofhire|date_of_hire", lambda f: f.__identity__.get("hire_date") or f.date_between(start_date="-5y", end_date="-1d").strftime("%m/%d/%Y")),
    (
        "effectivedate|effective_date|coverageeffective|coverage_effective",
        lambda f: f.__identity__.get("effective_date") or date.today().replace(month=1, day=1).strftime("%m/%d/%Y"),
    ),
    (
        "enrollmentdate|enrollment_date|coveragestartdate|electiondate",
        lambda f: f.__identity__.get("enrollment_date") or f.__identity__.get("effective_date") or f.date_between(start_date="today", end_date="+180d").strftime("%m/%d/%Y"),
    ),
    (
        "termdate|term_date|terminationdate|end_date|enddate|retiredate|retirementdate",
        lambda f: f.__identity__.get("retirement_date") or f.date_between(start_date="+1y", end_date="+3y").strftime("%m/%d/%Y"),
    ),

    # Compensation
    ("salary|annualsalary|compensation", lambda f: str(f.random_int(min=30_000, max=150_000))),
    ("hourlyrate|hourly_rate", lambda f: str(f.random_int(min=12, max=80))),
    ("hoursperweek|hours_per_week|hours", lambda f: str(f.random_element([20, 30, 32, 40]))),
]

_COMPILED = [(frozenset(pattern.split("|")), generator) for pattern, generator in _PATTERNS]


class FakerValueGenerator:
    """
    Deterministic value generator for workflow replay.

    Data strategy
    -------------
    1. Build a coherent base profile (faker).
    2. Optionally overlay Ollama-generated profile values.
    3. Apply execution-profile overrides.
    4. Validate/correct profile dates and formatting.

    Repeatability guarantee
    -----------------------
    A fixed seed produces the same profile and field values for the same
    workflow field identities.
    """

    def __init__(
        self,
        locale: str = "en_US",
        seed: Optional[int] = None,
        execution_profile: Optional[Dict[str, Any]] = None,
        use_ollama: bool = False,
        ollama_model: str = "",
        ollama_url: str = "http://127.0.0.1:11434/api/generate",
        ollama_timeout_s: float = 8.0,
    ) -> None:
        self._locale = locale
        self._explicit_seed = seed is not None and str(seed).strip() != ""
        self.seed = _safe_int(seed, random.SystemRandom().randint(1, 2_147_483_647))
        self._rand = random.Random(self.seed)
        self._base_fake = Faker(locale)
        self._base_fake.seed_instance(self.seed)

        self._execution_profile = dict(execution_profile or {})
        scenario_hint = " ".join(
            str(v)
            for v in (
                self._execution_profile.get("_workflow_name"),
                self._execution_profile.get("workflow_name"),
                self._execution_profile.get("task"),
                self._execution_profile.get("context"),
                self._execution_profile.get("start_url"),
            )
            if v
        )
        self._date_scenario = infer_date_scenario(
            hint_text=scenario_hint,
            hint_fields=list(self._execution_profile.keys()),
        )
        self._use_ollama = bool(use_ollama)
        self._ollama_model = str(ollama_model or "").strip()
        self._active_ollama_model = self._ollama_model
        self._ollama_url = str(ollama_url or "http://127.0.0.1:11434/api/generate").strip()
        self._ollama_timeout_s = max(1.0, float(ollama_timeout_s or 8.0))

        self._field_fake_cache: Dict[str, Faker] = {}
        self._field_value_cache: Dict[str, str] = {}
        self._corrections: List[str] = []
        self._warnings: List[str] = []
        self._source = "faker"

        profile = self._build_local_profile()
        if self._use_ollama:
            ollama_profile, warning = self._generate_profile_with_ollama()
            if ollama_profile:
                for key, value in ollama_profile.items():
                    if str(value or "").strip():
                        profile[key] = str(value).strip()
                self._source = "ollama"
            elif warning:
                self._warnings.append(warning)

        profile = self._apply_execution_profile_overrides(profile, self._execution_profile)
        profile = self._validate_and_correct_profile(profile)

        self.profile: Dict[str, str] = profile
        self.identity: Dict[str, str] = self._identity_from_profile(profile)
        self._base_fake.__identity__ = self.identity  # type: ignore[attr-defined]

    # -- Public ---------------------------------------------------------------

    def metadata(self) -> Dict[str, Any]:
        return {
            "source": self._source,
            "seed": int(self.seed),
            "date_scenario": self._date_scenario,
            "corrections": list(self._corrections),
            "warnings": list(self._warnings),
            "ollama_enabled": bool(self._use_ollama),
            "ollama_model": self._ollama_model,
            "ollama_model_active": self._active_ollama_model,
        }

    def profile_date(self, kind: str) -> str:
        mapping = {
            "dob": "dob_date",
            "hire": "hire_date",
            "effective": "effective_date",
            "enrollment": "enrollment_date",
            "retirement": "retirement_date",
        }
        return str(self.profile.get(mapping.get(str(kind or "").strip().lower(), ""), "") or "")

    def generate(self, step: "WorkflowStep") -> Optional[str]:
        from .models import StepType

        if step.is_credential_field:
            return None

        st = step.step_type
        if st not in (StepType.INPUT, StepType.DATE, None):
            return None

        key = self._step_identity_key(step)
        if key in self._field_value_cache:
            return self._field_value_cache[key]

        candidates = [
            _norm(step.id),
            _norm(step.name),
            _norm(step.label),
            _norm(step.placeholder),
            _norm(step.aria_label),
        ]

        for norm_id in candidates:
            if not norm_id:
                continue
            profile_value = self._resolve_profile_value(norm_id)
            if profile_value is not None:
                self._field_value_cache[key] = profile_value
                return profile_value

            for substrings, generator in _COMPILED:
                if any(sub in norm_id for sub in substrings):
                    try:
                        value = str(generator(self._faker_for_key(norm_id)))
                        self._field_value_cache[key] = value
                        return value
                    except Exception:
                        continue

        value = self._deterministic_fallback(step, key)
        self._field_value_cache[key] = value
        return value

    def date_value(self, minimum_age: int = 25, maximum_age: int = 65) -> str:
        dob = _to_date(self.profile.get("dob_date"))
        if dob:
            today = date.today()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if minimum_age <= age <= maximum_age:
                return _fmt_date(dob)

        f = self._faker_for_key(f"dob_{minimum_age}_{maximum_age}")
        return f.date_of_birth(minimum_age=minimum_age, maximum_age=maximum_age).strftime("%m/%d/%Y")

    # -- Profile construction -------------------------------------------------

    def _build_local_profile(self) -> Dict[str, str]:
        if self._explicit_seed:
            first = self._base_fake.first_name()
            last = self._base_fake.last_name()
            ssn = self._base_fake.ssn()
            phone = _safe_phone(self._base_fake)
        else:
            identity = _generate_unique_identity(self._base_fake)
            first = identity["first"]
            last = identity["last"]
            ssn = identity["ssn"]
            phone = identity["phone"]

        timeline, _ = build_realistic_timeline(
            rand=self._rand,
            overrides=self._execution_profile,
            scenario=self._date_scenario,
        )
        timeline_fields = timeline_to_profile_fields(timeline)

        zip_code = self._rand.choice(_US_HEALTHCARE_ZIPS)
        zip_ext = f"{self._rand.randint(0, 9999):04d}"
        address1 = self._base_fake.street_address()
        address2 = self._base_fake.secondary_address()
        city = self._base_fake.city()
        county = city
        state = self._base_fake.state_abbr()

        email_local = re.sub(r"[^a-z0-9]+", ".", f"{first}.{last}".lower()).strip(".") or "employee"
        email = f"{email_local}{self._rand.randint(10, 999)}@example.org"

        employee_id = f"EMP{self._rand.randint(10000, 99999)}"
        bargaining_unit = self._rand.choice(["Administrative", "Classified", "Instructional", "Support"])
        job_title = self._rand.choice(["Analyst", "Coordinator", "Manager", "Specialist"])

        profile = {
            "first_name": first,
            "middle_name": self._base_fake.first_name()[:1],
            "last_name": last,
            "full_name": f"{first} {last}".strip(),
            "ssn": ssn,
            "phone": phone,
            "work_phone": phone,
            "cell_phone": phone,
            "email": email,
            "address1": address1,
            "address2": address2,
            "city": city,
            "county": county,
            "state": state,
            "zip": zip_code,
            "zip_extension": zip_ext,
            "employee_id": employee_id,
            "bargaining_unit": bargaining_unit,
            "job_title": job_title,
            "prefix": self._base_fake.prefix(),
            "suffix": "",
            "billing_location": "",
            "employee_class": self._rand.choice(["Full Time", "Part Time", "Retiree"]),
        }
        profile.update(timeline_fields)
        return profile

    def _apply_execution_profile_overrides(
        self,
        profile: Dict[str, str],
        overrides: Dict[str, Any],
    ) -> Dict[str, str]:
        normalized = {
            _norm(str(k)): str(v).strip()
            for k, v in (overrides or {}).items()
            if v not in (None, "")
        }

        alias_map = {
            "first_name": ["first_name", "firstname", "first"],
            "middle_name": ["middle_name", "middlename", "middleinitial"],
            "last_name": ["last_name", "lastname", "surname", "family_name"],
            "full_name": ["full_name", "fullname"],
            "email": ["email", "email_address"],
            "phone": ["phone", "cell_phone", "mobile", "telephone"],
            "ssn": ["ssn", "social_security", "socialsecuritynumber"],
            "address1": ["address1", "street_address", "address_line_1"],
            "address2": ["address2", "address_line_2", "apt", "suite"],
            "city": ["city"],
            "county": ["county"],
            "state": ["state", "province"],
            "zip": ["zip", "zipcode", "postalcode"],
            "zip_extension": ["zip_extension", "zipextension"],
            "dob_date": ["dob_date", "dob", "birth_date", "dateofbirth", "date_of_birth"],
            "hire_date": ["hire_date", "date_of_hire", "dateofhire", "employment_date", "start_date"],
            "effective_date": ["effective_date", "effectivedate", "coverage_effective"],
            "enrollment_date": ["enrollment_date", "enrollmentdate", "coverage_start_date", "election_date"],
            "retirement_date": ["retirement_date", "retiredate", "terminationdate", "termdate"],
            "employee_id": ["employee_id", "employeeid", "empid"],
            "bargaining_unit": ["bargaining_unit", "bargainingunit", "unioncode"],
            "billing_location": ["billing_location", "billinglocation", "subgroup", "subgroupid"],
            "employee_class": ["employee_class", "employeeclass", "classid"],
            "prefix": ["prefix", "title"],
            "suffix": ["suffix"],
            "job_title": ["job_title", "jobtitle", "occupation", "position"],
        }

        merged = dict(profile)
        for target, aliases in alias_map.items():
            for alias in aliases:
                val = normalized.get(_norm(alias))
                if val:
                    merged[target] = val
                    break

        return merged

    def _validate_and_correct_profile(self, profile: Dict[str, str]) -> Dict[str, str]:
        out = dict(profile)
        corrections: List[str] = []

        timeline, timeline_corrections = build_realistic_timeline(
            rand=self._rand,
            overrides=out,
            scenario=self._date_scenario,
        )
        out.update(timeline_to_profile_fields(timeline))
        corrections.extend(timeline_corrections)

        zip5 = _digits(out.get("zip", ""))[:5]
        default_zip = _digits(DEFAULT_VALID_ZIP)[:5] or "20705"
        if len(zip5) != 5:
            zip5 = default_zip
            corrections.append("ZIP corrected to a valid 5-digit value")
        out["zip"] = zip5

        zip_ext = _digits(out.get("zip_extension", ""))[:4]
        if len(zip_ext) != 4:
            zip_ext = f"{self._rand.randint(0, 9999):04d}"
        out["zip_extension"] = zip_ext

        phone = str(out.get("phone", "")).strip()
        if len(_digits(phone)) < 10:
            phone = _safe_phone(self._faker_for_key("fallback_phone"))
            corrections.append("Phone corrected to valid format")
        out["phone"] = phone
        out.setdefault("work_phone", phone)
        out.setdefault("cell_phone", phone)

        ssn = str(out.get("ssn", "")).strip()
        if len(_digits(ssn)) != 9:
            ssn = self._faker_for_key("fallback_ssn").ssn()
            corrections.append("SSN corrected to valid format")
        out["ssn"] = ssn

        first = str(out.get("first_name", "")).strip() or self._base_fake.first_name()
        last = str(out.get("last_name", "")).strip() or self._base_fake.last_name()
        middle = str(out.get("middle_name", "")).strip()[:1] or self._base_fake.first_name()[:1]
        out["first_name"] = first
        out["last_name"] = last
        out["middle_name"] = middle
        out["full_name"] = str(out.get("full_name", "")).strip() or f"{first} {last}".strip()

        email = str(out.get("email", "")).strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            email_local = re.sub(r"[^a-z0-9]+", ".", f"{first}.{last}".lower()).strip(".") or "employee"
            email = f"{email_local}{self._rand.randint(10, 999)}@example.org"
            corrections.append("Email corrected to valid format")
        out["email"] = email

        out.setdefault("address1", self._base_fake.street_address())
        out.setdefault("address2", self._base_fake.secondary_address())
        out.setdefault("city", self._base_fake.city())
        out.setdefault("county", out.get("city", ""))
        out.setdefault("state", self._base_fake.state_abbr())
        out.setdefault("employee_id", f"EMP{self._rand.randint(10000, 99999)}")
        out.setdefault("bargaining_unit", "Administrative")
        out.setdefault("job_title", "Analyst")
        out.setdefault("prefix", "Mr.")
        out.setdefault("suffix", "")
        out.setdefault("billing_location", "")
        out.setdefault("employee_class", "Retiree")

        self._corrections.extend(corrections)
        return out

    def _identity_from_profile(self, profile: Dict[str, str]) -> Dict[str, str]:
        return {
            "first": str(profile.get("first_name", "") or ""),
            "middle_name": str(profile.get("middle_name", "") or ""),
            "last": str(profile.get("last_name", "") or ""),
            "full_name": str(profile.get("full_name", "") or ""),
            "email": str(profile.get("email", "") or ""),
            "phone": str(profile.get("phone", "") or ""),
            "ssn": str(profile.get("ssn", "") or ""),
            "address1": str(profile.get("address1", "") or ""),
            "address2": str(profile.get("address2", "") or ""),
            "city": str(profile.get("city", "") or ""),
            "county": str(profile.get("county", "") or ""),
            "state": str(profile.get("state", "") or ""),
            "zip": str(profile.get("zip", "") or ""),
            "zip_extension": str(profile.get("zip_extension", "") or ""),
            "dob_date": str(profile.get("dob_date", "") or ""),
            "hire_date": str(profile.get("hire_date", "") or ""),
            "effective_date": str(profile.get("effective_date", "") or ""),
            "enrollment_date": str(profile.get("enrollment_date", "") or ""),
            "retirement_date": str(profile.get("retirement_date", "") or ""),
            "enrollment_window_start": str(profile.get("enrollment_window_start", "") or ""),
            "enrollment_window_end": str(profile.get("enrollment_window_end", "") or ""),
            "employee_id": str(profile.get("employee_id", "") or ""),
            "bargaining_unit": str(profile.get("bargaining_unit", "") or ""),
            "billing_location": str(profile.get("billing_location", "") or ""),
            "employee_class": str(profile.get("employee_class", "") or ""),
            "prefix": str(profile.get("prefix", "") or ""),
            "suffix": str(profile.get("suffix", "") or ""),
            "job_title": str(profile.get("job_title", "") or ""),
        }

    # -- Ollama ---------------------------------------------------------------

    def _generate_profile_with_ollama(self) -> Tuple[Optional[Dict[str, str]], str]:
        if not self._ollama_model:
            return None, "Ollama enabled but no model configured; using faker profile"

        prompt = (
            "Generate one synthetic employee profile as strict JSON only. "
            "Keys required: first_name,last_name,dob_date,hire_date,effective_date,enrollment_date,address1,address2,city,state,zip,phone,email,ssn,employee_id,bargaining_unit,billing_location,employee_class. "
            "Rules: keep DOB realistic for workforce (prefer ages 25-65, minimum 18); hire_date must be after dob_date + 18 years and <= today; effective_date must be > hire_date; enrollment_date must be > effective_date and near effective_date. "
            "Use MM/DD/YYYY for date fields. Use realistic US formatting."
        )

        options: Dict[str, Any] = {"temperature": 0}
        if self._explicit_seed:
            options["seed"] = int(self.seed)

        parsed, request_error = self._request_ollama_generate(self._ollama_model, prompt, options)
        if parsed is None and self._looks_like_missing_model_error(request_error):
            fallback_model = self._resolve_fallback_ollama_model(self._ollama_model)
            if fallback_model and fallback_model != self._ollama_model:
                parsed, retry_error = self._request_ollama_generate(fallback_model, prompt, options)
                if parsed is not None:
                    self._active_ollama_model = fallback_model
                    self._warnings.append(
                        f"Configured Ollama model '{self._ollama_model}' not found; used '{fallback_model}'"
                    )
                else:
                    request_error = retry_error

        if parsed is None:
            err = request_error or "unknown error"
            return None, f"Ollama request failed ({err}); using faker profile"

        self._active_ollama_model = str(parsed.get("model") or self._active_ollama_model or self._ollama_model)

        candidate: Dict[str, Any] = {}
        if isinstance(parsed, dict):
            if isinstance(parsed.get("response"), str):
                candidate = _extract_json_object(parsed.get("response", ""))
            elif isinstance(parsed.get("message"), dict):
                content = str(parsed.get("message", {}).get("content", ""))
                candidate = _extract_json_object(content)

        if not candidate:
            return None, "Ollama returned empty/invalid profile JSON; using faker profile"

        return {str(k): str(v) for k, v in candidate.items() if v not in (None, "")}, ""

    def _request_ollama_generate(
        self,
        model: str,
        prompt: str,
        options: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": options,
        }
        req = urlrequest.Request(
            self._ollama_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlrequest.urlopen(req, timeout=self._ollama_timeout_s) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urlerror.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                detail = ""
            if detail:
                return None, f"HTTP {exc.code}: {detail}"
            return None, f"HTTP Error {exc.code}: {exc.reason}"
        except (urlerror.URLError, TimeoutError, OSError) as exc:
            return None, str(exc)

        try:
            parsed = json.loads(body)
        except Exception:
            return None, "Ollama response was not JSON"

        return parsed if isinstance(parsed, dict) else {}, ""

    @staticmethod
    def _looks_like_missing_model_error(message: str) -> bool:
        text = str(message or "").lower()
        return "model" in text and "not found" in text and ("http 404" in text or "http error 404" in text)

    def _resolve_fallback_ollama_model(self, configured_model: str) -> str:
        names = self._list_ollama_models()
        if not names:
            return ""

        configured = str(configured_model or "").strip().lower()
        if not configured:
            return names[0]

        # Retry exact match in case the 404 response came from stale model cache.
        for name in names:
            if name.lower() == configured:
                return name

        # Prefer same base alias before trying first available.
        configured_base = configured.split(":", 1)[0]
        for name in names:
            if name.lower().split(":", 1)[0] == configured_base:
                return name

        return names[0]

    def _list_ollama_models(self) -> List[str]:
        tags_url = self._ollama_tags_url()
        req = urlrequest.Request(tags_url, method="GET")
        try:
            with urlrequest.urlopen(req, timeout=min(self._ollama_timeout_s, 20.0)) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return []

        try:
            parsed = json.loads(body)
        except Exception:
            return []

        models = parsed.get("models") if isinstance(parsed, dict) else []
        if not isinstance(models, list):
            return []

        out: List[str] = []
        for row in models:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if name:
                out.append(name)
        return out

    def _ollama_tags_url(self) -> str:
        parsed = urlparse.urlsplit(self._ollama_url)
        if not parsed.scheme or not parsed.netloc:
            return "http://127.0.0.1:11434/api/tags"

        path = parsed.path or ""
        if "/api/" in path:
            prefix = path.split("/api/", 1)[0]
            tags_path = f"{prefix}/api/tags" if prefix else "/api/tags"
        else:
            trimmed = path.rstrip("/")
            tags_path = f"{trimmed}/api/tags" if trimmed else "/api/tags"

        return f"{parsed.scheme}://{parsed.netloc}{tags_path}"

    # -- Field resolution -----------------------------------------------------

    def _step_identity_key(self, step: "WorkflowStep") -> str:
        parts = [
            _norm(step.id),
            _norm(step.name),
            _norm(step.label),
            _norm(step.placeholder),
            _norm(step.aria_label),
            _norm(step.selector),
        ]
        key = "|".join([p for p in parts if p])
        return key or f"step_{max(0, int(step.index))}"

    def _resolve_profile_value(self, norm_id: str) -> Optional[str]:
        text = norm_id or ""

        if any(token in text for token in ("dob", "birthdate", "dateofbirth", "birth_date")):
            return self.profile.get("dob_date", "")
        if any(token in text for token in ("hiredate", "dateofhire", "hire_date", "employmentdate", "startdate")):
            return self.profile.get("hire_date", "")
        if any(token in text for token in ("effectivedate", "effective_date", "coverageeffective", "coverage_effective")):
            return self.profile.get("effective_date", "")
        if any(token in text for token in ("enrollmentdate", "enrollment_date", "coveragestartdate", "electiondate")):
            return self.profile.get("enrollment_date", "")
        if any(token in text for token in ("termdate", "terminationdate", "retiredate", "retirementdate")):
            return self.profile.get("retirement_date", "")

        if any(token in text for token in ("firstname", "first_name", "givenname", "fname")):
            return self.profile.get("first_name", "")
        if any(token in text for token in ("middlename", "middle_name", "middleinitial", "mid_init")):
            return self.profile.get("middle_name", "")
        if any(token in text for token in ("lastname", "last_name", "surname", "familyname", "lname")):
            return self.profile.get("last_name", "")
        if any(token in text for token in ("fullname", "full_name")):
            return self.profile.get("full_name", "")

        if "email" in text:
            return self.profile.get("email", "")
        if any(token in text for token in ("phone", "cell", "mobile", "telephone", "tel", "altphone", "workphone")):
            return self.profile.get("phone", "")
        if any(token in text for token in ("ssn", "socialsecurity", "social_security")):
            return self.profile.get("ssn", "")

        if any(token in text for token in ("employeeid", "employee_id", "empid", "staffid", "workerid")):
            return self.profile.get("employee_id", "")
        if any(token in text for token in ("bargainingunit", "bargaining_unit", "unioncode")):
            return self.profile.get("bargaining_unit", "")
        if any(token in text for token in ("subgroup", "subgroupid", "billinglocation", "billing_location")):
            return self.profile.get("billing_location", "")
        if any(token in text for token in ("employeeclass", "employee_class", "classid", "employeetype")):
            return self.profile.get("employee_class", "")

        if any(token in text for token in ("address1", "address_1", "streetaddress", "addressline1", "addr1")):
            return self.profile.get("address1", "")
        if any(token in text for token in ("address2", "address_2", "addressline2", "apt", "suite", "unit")):
            return self.profile.get("address2", "")
        if "city" in text:
            return self.profile.get("city", "")
        if "county" in text:
            return self.profile.get("county", "")
        if any(token in text for token in ("state", "province")):
            return self.profile.get("state", "")
        if any(token in text for token in (
            "zipext",
            "zip_ext",
            "zip_extension",
            "zipextension",
            "zipcodeextension",
            "zipcode_extension",
        )):
            return self.profile.get("zip_extension", "")
        if any(token in text for token in ("zip", "zipcode", "zip_code", "postalcode", "postal_code")):
            return self.profile.get("zip", "")

        return None

    def _faker_for_key(self, key: str) -> Faker:
        norm_key = _norm(key) or "fallback"
        cached = self._field_fake_cache.get(norm_key)
        if cached is not None:
            return cached

        derived_seed = _safe_int(abs(hash((self.seed, norm_key))) % 2_147_483_647, self.seed)
        fake = Faker(self._locale)
        fake.seed_instance(derived_seed)
        fake.__identity__ = self.identity  # type: ignore[attr-defined]
        self._field_fake_cache[norm_key] = fake
        return fake

    def _deterministic_fallback(self, step: "WorkflowStep", key: str) -> str:
        itype = (step.input_type or "").lower()
        fake = self._faker_for_key(key)

        if itype == "email":
            return self.profile.get("email", "") or fake.email()
        if itype in ("tel", "phone"):
            return self.profile.get("phone", "") or _safe_phone(fake)
        if itype == "date":
            norm_key = _norm(key)
            if any(token in norm_key for token in ("dob", "birthdate", "dateofbirth")):
                return self.profile.get("dob_date", "")
            if any(token in norm_key for token in ("hiredate", "dateofhire", "employmentdate", "startdate")):
                return self.profile.get("hire_date", "")
            if any(token in norm_key for token in ("enrollmentdate", "coveragestartdate", "electiondate")):
                return self.profile.get("enrollment_date", "") or self.profile.get("effective_date", "")
            if any(token in norm_key for token in ("retiredate", "retirementdate", "terminationdate", "termdate")):
                return self.profile.get("retirement_date", "")
            return self.profile.get("effective_date", "") or _fmt_date(date.today())
        if itype == "number":
            return str(fake.random_int(min=1, max=99))
        if itype == "url":
            return "https://example.org"

        recorded = str(step.value or "").strip()
        if recorded:
            return recorded
        return fake.word().capitalize()
