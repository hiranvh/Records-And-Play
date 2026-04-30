import json
import os
import re
import random
from datetime import date
from typing import Dict, Optional

import pandas as pd
from core.constants import DEFAULT_VALID_ZIP
from core.profile import generate_profile, normalize_zip_extension

from .llm.llm_instance import get_llm_instance, wait_for_llm_availability

class AgentEngine:
    def __init__(self, update_callback=None):
        self.update_callback = update_callback
        self.default_zip = os.environ.get("AGENT_DEFAULT_ZIP", DEFAULT_VALID_ZIP).strip() or DEFAULT_VALID_ZIP
        self.default_zip_extension = ""

    def _log(self, msg, level=None):
        print(msg)
        if self.update_callback:
            self.update_callback(msg)

    def _canonicalize_execution_keys(self, data: Optional[Dict]) -> Dict:
        """Normalize profile-like keys so downstream field matching is consistent."""
        alias_groups = {
            "first_name": ["firstname", "first_name", "first", "givenname", "given_name"],
            "middle_name": ["middlename", "middle_name", "middle", "middleinitial", "middle_initial"],
            "last_name": ["lastname", "last_name", "surname", "familyname", "family_name"],
            "dob": ["dob", "dob_date", "birthdate", "birth_date", "dateofbirth", "date_of_birth"],
            "hire_date": ["hiredate", "hire_date", "dateofhire", "date_of_hire", "employmentdate"],
            "effective_date": ["effectivedate", "effective_date", "coverageeffectivedate"],
            "gender": ["gender", "sex"],
            "ssn": ["ssn", "socialsecurity", "social_security", "socialsecuritynumber"],
            "phone": ["phone", "cellphone", "cell_phone", "mobile", "mobilephone", "mobile_phone"],
            "email": ["email", "emailaddress", "email_address"],
            "address1": ["address1", "address_1", "address", "addressline1", "address_line_1", "streetaddress"],
            "address2": ["address2", "address_2", "addressline2", "address_line_2", "secondaryaddress"],
            "city": ["city"],
            "state": ["state", "province"],
            "zip": ["zip", "zipcode", "zip_code", "postalcode", "postal_code"],
            "zip_extension": ["zipextension", "zip_extension", "zipcodeextension", "zip_code_extension"],
            "county": ["county"],
            "marital": ["marital", "maritalstatus", "marital_status"],
            "billing_location": ["billinglocation", "billing_location", "subgroup", "subgroupid", "sub_group"],
            "employee_class": ["employeeclass", "employee_class", "classid", "class_id"],
            "group_name": ["group_name", "groupname", "group"],
            "age": ["age", "memberage", "member_age"],
        }
        alias_to_canonical = {
            alias: canonical
            for canonical, aliases in alias_groups.items()
            for alias in aliases
        }

        normalized: Dict = {}
        for raw_key, value in (data or {}).items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            compact = re.sub(r"[^a-z0-9]", "", key.lower())
            canonical = alias_to_canonical.get(key.lower()) or alias_to_canonical.get(compact) or key
            if canonical not in normalized or normalized.get(canonical) in (None, ""):
                normalized[canonical] = value

        if "group_name" in normalized:
            normalized.setdefault("group", normalized["group_name"])
        if "group" in normalized:
            normalized.setdefault("group_name", normalized["group"])
        if "dob" not in normalized and normalized.get("dob_date"):
            normalized["dob"] = normalized["dob_date"]
        if "dob_date" not in normalized and normalized.get("dob"):
            normalized["dob_date"] = normalized["dob"]

        zip_digits = re.sub(r"\D", "", str(normalized.get("zip") or ""))
        if len(zip_digits) >= 5:
            normalized["zip"] = zip_digits[:5]

        if "zip_extension" in normalized:
            normalized["zip_extension"] = normalize_zip_extension(
                normalized.get("zip_extension") or "",
                normalized.get("zip") or "",
            )

        return normalized

    def load_model(self):
        """Lightweight health check for local LLM endpoint."""
        if wait_for_llm_availability(timeout_seconds=45, retry_interval=1.5):
            self._log("Model endpoint reachable.")
            return True
        raise RuntimeError("LLM endpoint unavailable after waiting for Ollama to start.")

    def parse_commander_intent(self, user_request: str) -> Dict:
        """
        Parse the command into task/count/entity overrides.

        Returns keys:
          - task: e.g. enrollment
          - count: number of records to create
          - workflow_name: suggested workflow file
          - overrides: explicit user-provided values (first_name, last_name, age, group_name, ...)
          - auto_generate_missing: bool
        """
        fallback = self._parse_intent_regex(user_request)
        prompt = (
            f"Analyze this automation command: {json.dumps(user_request)}\n"
            "Extract intent and fields. Return ONLY JSON with keys: "
            "task (str), count (int), workflow_name (str), overrides (object), auto_generate_missing (bool).\n"
            "If command is about enrollment/customer creation, task='enrollment'.\n"
            "If user did not provide some fields, keep them out of overrides and set auto_generate_missing=true.\n"
            "Example command: Create an Enrollment with First name 'Ana' Last name 'Kim' age 26 under groupname 'Sunrise Corp'\n"
            "Example output: {\"task\":\"enrollment\",\"count\":1,\"workflow_name\":\"Enrollment.json\","
            "\"overrides\":{\"first_name\":\"Ana\",\"last_name\":\"Kim\",\"age\":26,\"group_name\":\"Sunrise Corp\"},"
            "\"auto_generate_missing\":true}"
        )

        try:
            parsed = self._call_llm_json(prompt)
            if not isinstance(parsed, dict):
                return fallback

            task = str(parsed.get("task") or fallback["task"]).strip().lower() or "enrollment"
            count = parsed.get("count", fallback["count"])
            try:
                count = max(1, int(count))
            except Exception:
                count = fallback["count"]

            workflow_name = str(parsed.get("workflow_name") or "").strip()
            if not workflow_name:
                workflow_name = fallback["workflow_name"]

            overrides = parsed.get("overrides") if isinstance(parsed.get("overrides"), dict) else {}
            if not overrides:
                overrides = fallback.get("overrides", {})
            else:
                # Regex fallback enriches fields the LLM missed.
                for k, v in fallback.get("overrides", {}).items():
                    overrides.setdefault(k, v)

            # Regex extraction of quoted strings is more reliable than the LLM for
            # group_name / group because small local LLMs tend to copy the example value.
            # Always prefer the regex-parsed value when it is available.
            regex_group = fallback.get("overrides", {}).get("group_name")
            if regex_group:
                overrides["group_name"] = regex_group
                overrides["group"] = regex_group

            auto_generate_missing = bool(parsed.get("auto_generate_missing", True))
            if task in {"enrollment", "customer", "enrolment"}:
                task = "enrollment"

            return {
                "task": task,
                "count": count,
                "workflow_name": self._normalize_workflow_name(workflow_name or f"{task}.json"),
                "overrides": overrides,
                "auto_generate_missing": auto_generate_missing,
            }
        except Exception as e:
            self._log(f"Commander Parse Error: {e}")
            return fallback

    def _call_llm_json(self, prompt: str) -> Dict:
        if not wait_for_llm_availability(timeout_seconds=45, retry_interval=1.5):
            raise RuntimeError("LLM endpoint unavailable after waiting for Ollama to start.")
        llm = get_llm_instance(required=True, timeout_seconds=45, retry_interval=1.5)
        if llm is None:
            raise RuntimeError("LLM client unavailable.")

        raw = llm.generate(prompt, format="json")
        if not raw:
            return {}
        return json.loads(raw)

    def _normalize_workflow_name(self, name: str) -> str:
        cleaned = (name or "workflow").strip()
        if not cleaned.endswith(".json"):
            cleaned += ".json"
        return cleaned

    def _parse_intent_regex(self, user_request: str) -> Dict:
        text = user_request or ""
        norm = text.lower()

        task = "enrollment" if any(k in norm for k in ["enrollment", "enrolment", "customer"]) else "workflow"

        count = 1
        m_count = re.search(r"\b(?:create|run|do|make)\s+(\d+)\b", norm)
        if m_count:
            try:
                count = max(1, int(m_count.group(1)))
            except Exception:
                count = 1

        def _extract(patterns):
            for p in patterns:
                m = re.search(p, text, flags=re.IGNORECASE)
                if m:
                    return (m.group(1) or "").strip()
            return ""

        first_name = _extract([
            r"first\s*name\s*[=:]?\s*[\"']([^\"']+)[\"']",
            r"first\s*name\s*[=:]?\s*([A-Za-z.-]+)",
        ])
        last_name = _extract([
            r"last\s*name\s*[=:]?\s*[\"']([^\"']+)[\"']",
            r"last\s*name\s*[=:]?\s*([A-Za-z.-]+)",
        ])
        group_name = _extract([
            # Quoted: group name 'Astral Filters' or group name "Astral Filters"
            r"group\s*name\s*[=:]?\s*[\"']([^\"']+)[\"']",
            # Quoted with different phrasing: under group name 'X'
            r"under\s+group\s*name\s*[\"']([^\"']+)[\"']",
            # under groupname 'X'
            r"under\s+groupname\s*[\"']([^\"']+)[\"']",
            # group: 'X' or group = 'X'
            r"group\s*[=:]\s*[\"']([^\"']+)[\"']",
            # Unquoted: under group name Astral Filters (stop at end or punctuation)
            r"under\s+group\s*name\s+([A-Za-z0-9][A-Za-z0-9 _.-]*?)(?:\s*[,.'\"(]|$)",
            # Unquoted fallback: under group Astral Filters
            r"under\s+group\s+([A-Za-z0-9][A-Za-z0-9 _.-]*?)(?:\s*[,.'\"(]|$)",
        ])

        age_val = None
        m_age = re.search(r"\bage\s*[=:]?\s*(\d{1,3})\b", norm)
        if m_age:
            try:
                age_val = int(m_age.group(1))
            except Exception:
                age_val = None

        age_max = None
        m_age_lt = re.search(r"\bage\s*(?:<|<=|less than|under|below|younger than)\s*(\d{1,3})\b", norm)
        if m_age_lt:
            try:
                age_max = int(m_age_lt.group(1))
            except Exception:
                age_max = None

        age_min = None
        m_age_gt = re.search(r"\bage\s*(?:>|>=|greater than|over|above|older than)\s*(\d{1,3})\b", norm)
        if m_age_gt:
            try:
                age_min = int(m_age_gt.group(1))
            except Exception:
                age_min = None

        all_plans = bool(re.search(r"\b(all plans|enroll .* all plans|all coverages|all coverage)\b", norm))

        overrides = {}
        if first_name:
            overrides["first_name"] = first_name
        if last_name:
            overrides["last_name"] = last_name
        if group_name:
            overrides["group_name"] = group_name
            overrides["group"] = group_name
        if age_val is not None:
            overrides["age"] = age_val
            # Approximate DOB from age if user did not provide DOB explicitly.
            approx_year = max(1930, date.today().year - max(0, min(age_val, 100)))
            overrides.setdefault("dob", f"01/01/{approx_year}")
        if age_min is not None:
            overrides["age_min"] = max(0, min(age_min, 120))
        if age_max is not None:
            overrides["age_max"] = max(0, min(age_max, 120))
        if all_plans:
            overrides["enroll_all_plans"] = True
            overrides["coverage_mode"] = "all"

        return {
            "task": task,
            "count": count,
            "workflow_name": self._normalize_workflow_name("Enrollment.json" if task == "enrollment" else f"{task}.json"),
            "overrides": overrides,
            "auto_generate_missing": True,
        }

    def load_spreadsheet_data(self, file_name: str = "data.xlsx") -> list:
        """Load optional spreadsheet input rows for looped executions."""
        path = os.path.join(os.getcwd(), file_name)
        if not os.path.exists(path):
            self._log(f"Excel data '{file_name}' not found. Using defaults.")
            return []
        try:
            df = pd.read_excel(path)
            return df.to_dict(orient="records")
        except Exception as e:
            self._log(f"Error reading Excel: {e}")
            return []

    def resolve_workflow_name(self, preferred_name: str, fallback_task: str = "enrollment") -> str:
        """Resolve workflow filename by checking existing files case-insensitively."""
        workflows_dir = os.path.join(os.getcwd(), "workflows")
        os.makedirs(workflows_dir, exist_ok=True)

        preferred = self._normalize_workflow_name(preferred_name or "")
        if os.path.exists(os.path.join(workflows_dir, preferred)):
            return preferred

        # Common enrollment defaults (supports naming workflow as Enrollment)
        candidates = [
            preferred,
            "Enrollment.json",
            "enrollment.json",
            f"{fallback_task}.json",
            "workflow.json",
        ]

        lower_map = {f.lower(): f for f in os.listdir(workflows_dir) if f.endswith(".json")}
        for c in candidates:
            if c.lower() in lower_map:
                return lower_map[c.lower()]

        return "workflow.json"

    def build_intelligent_execution_data(self, overrides: Optional[Dict] = None, task: str = "enrollment") -> Dict:
        """
        Build a complete execution profile.
        - Keeps explicit user values only
        - Normalizes age constraints into concrete values when provided
        """
        merged = self._canonicalize_execution_keys(overrides)

        # Normalize age constraints into concrete age/dob when only ranges are provided.
        # This supports commands like "age less than 65" and "age above 21".
        try:
            age_min = int(merged.get("age_min")) if merged.get("age_min") is not None else None
        except Exception:
            age_min = None
        try:
            age_max = int(merged.get("age_max")) if merged.get("age_max") is not None else None
        except Exception:
            age_max = None

        if merged.get("age") is None and (age_min is not None or age_max is not None):
            lo = max(0, age_min if age_min is not None else 18)
            hi_raw = age_max if age_max is not None else 75
            # "less than 65" should produce max age 64.
            hi = max(lo, hi_raw - 1) if age_max is not None else max(lo, hi_raw)
            chosen_age = random.randint(lo, hi) if hi >= lo else lo
            merged["age"] = chosen_age
            approx_year = max(1930, date.today().year - max(0, min(chosen_age, 100)))
            merged.setdefault("dob", f"01/01/{approx_year}")

        if task not in {"enrollment", "customer", "enrolment"}:
            return merged

        # Friendly aliases used by some forms/workflows.
        if "group_name" in merged:
            merged.setdefault("group", merged["group_name"])
        if "group" in merged:
            merged.setdefault("group_name", merged["group"])
        if "dob" not in merged and merged.get("dob_date"):
            merged["dob"] = merged["dob_date"]
            
        # Default zip overrides can be configured via env vars.
        merged.setdefault("zip", self.default_zip)
        merged.setdefault("zip_extension", self.default_zip_extension)

        # Initialize random seed for the LLM to prevent caching or repetitive standard outputs (like "John Doe")
        run_seed = random.randint(100000, 999999)
        
        # If the user has explicitly requested to inject fake data or the profile is bare-bones,
        # we can use the model to invent cohesive fake data for missing fields.
        missing_keys_prompt = (
            f"Generate realistic fake profile data for an individual. Return ONLY valid JSON with no markdown formatting.\n"
            f"IMPORTANT: Ensure the first_name, last_name, and ssn are HIGHLY randomized and unique for this request (Seed: {run_seed}). Do not use generic names like John Doe.\n"
            "Include these keys: first_name, last_name, middle_name, dob (MM/DD/YYYY format, adult), ssn (XXX-XX-XXXX format), "
            "gender (Male/Female), email, address1 (street). "
            "Do not include keys if they are already present in the user overrides.\n"
            f"User provided overrides so far: {json.dumps(merged)}"
        )
        try:
            self._log("Generating fake profile data via LLM...", "AI")
            fake_data = self._call_llm_json(missing_keys_prompt)
            fake_data = self._canonicalize_execution_keys(fake_data)
            if isinstance(fake_data, dict) and fake_data:
                added = []
                for k, v in fake_data.items():
                    if k not in merged:
                        merged[k] = v
                        added.append(k)
                if added:
                    self._log(f"LLM generated keys: {added}", "AI")
            else:
                self._log("LLM returned empty/invalid data — applying Python fallback.", "WARNING")
                raise ValueError("empty")
        except Exception as e:
            self._log(f"LLM fake data failed ({e}), using Python fallback.", "WARNING")
            # Deterministic pool-based fallback so required fields are always populated.
            _first_names = ["Alice", "Brandon", "Carmen", "Derek", "Elena", "Frank", "Grace", "Henry",
                            "Irene", "James", "Karen", "Louis", "Mia", "Nolan", "Olivia", "Patrick"]
            _last_names  = ["Adams", "Brooks", "Castro", "Dixon", "Evans", "Foster", "Gibson", "Harris",
                            "Ingram", "Jensen", "Klein", "Lloyd", "Mason", "Nash", "Owen", "Parker"]
            _genders = ["Male", "Female"]
            _r = random.Random(run_seed)
            fn = _r.choice(_first_names)
            ln = _r.choice(_last_names)
            mn = _r.choice(_first_names)
            ssn_a, ssn_b, ssn_c = _r.randint(200, 799), _r.randint(10, 99), _r.randint(1000, 9999)
            birth_year = date.today().year - _r.randint(25, 60)
            birth_month = _r.randint(1, 12)
            birth_day = _r.randint(1, 28)
            house_num = _r.randint(100, 9999)
            _streets = ["Oak St", "Maple Ave", "Cedar Ln", "Pine Rd", "Elm Dr", "Birch Way"]
            street = _r.choice(_streets)
            fallback = {
                "first_name":  fn,
                "last_name":   ln,
                "middle_name": mn,
                "gender":      _r.choice(_genders),
                "dob":         f"{birth_month:02d}/{birth_day:02d}/{birth_year}",
                "ssn":         f"{ssn_a:03d}-{ssn_b:02d}-{ssn_c:04d}",
                "email":       f"{fn.lower()}.{ln.lower()}{_r.randint(10,99)}@mailtest.com",
                "address1":    f"{house_num} {street}",
            }
            added = []
            for k, v in fallback.items():
                if k not in merged:
                    merged[k] = v
                    added.append(k)
            if added:
                self._log(f"Fallback generated keys: {added}", "AI")

        # Guarantee a complete enrollment profile even when the LLM returns only a
        # sparse object (for example just zip/city/state).
        required_enrollment_keys = {
            "first_name", "last_name", "dob", "dob_date", "ssn", "gender", "phone",
            "email", "address1", "city", "state", "zip", "county", "marital",
            "zip_extension", "billing_location", "employee_class", "effective_date", "hire_date",
            "tobacco", "tobacco_label",
        }
        missing_required = [
            k for k in required_enrollment_keys
            if not str(merged.get(k, "")).strip()
        ]
        if missing_required:
            deterministic = generate_profile(override_data_normalized=merged)
            filled = []
            for k in missing_required:
                v = deterministic.get(k)
                if str(v or "").strip():
                    merged[k] = v
                    filled.append(k)
            if filled:
                self._log(f"Deterministic backfill keys: {filled}", "AI")

        merged = self._canonicalize_execution_keys(merged)
        self._log(f"Final profile keys: {sorted(merged.keys())}", "AI")
        return merged


