import json
import os
import time
import threading
import random
import re
from datetime import date, datetime, timedelta

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


stop_recording_event = threading.Event()
stop_execution_event = threading.Event()
DEFAULT_VALID_ZIP = "20705"
WORKFLOW_SCHEMA_VERSION = 2
ZIP_LOCATION_DATA = {
    "20705": {
        "county": "PRINCE GEORGE'S",
        "city": "BELTSVILLE",
        "state": "MD",
    }
}

try:
    from faker import Faker

    fake = Faker()
except ImportError:
    fake = None


def _normalize_text(value):
    return "".join(ch.lower() for ch in str(value) if ch.isalnum())


def _clean_text(value):
    return " ".join(str(value or "").split()).strip()


def _safe_lower(value):
    return _clean_text(value).lower()


def _candidate_values(*values):
    seen = set()
    candidates = []
    for value in values:
        cleaned = _clean_text(value)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)
    return candidates


def _css_attr_selector(attr, value, tag=None):
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    prefix = f"{tag}" if tag else ""
    # This returns a CSS selector fragment such as "[id=\"foo\"]" or "input[name=\"bar\"]".
    return f"{prefix}[{attr}={json.dumps(cleaned)}]"


def _create_webdriver(headless=False):
    """Create a Selenium WebDriver instance.

    Uses Chrome with Selenium Manager so the user doesn't need to manage
    chromedriver manually.
    """
    chrome_options = Options()
    if headless:
        # Use the new headless mode where available.
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
    else:
        chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=chrome_options)
    if not headless:
        try:
            driver.maximize_window()
        except Exception:
            pass
    return driver


def _wait_for_page_ready(driver, timeout=30):
    """Wait until the document is fully loaded."""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        # If the page never reaches a fully ready state, continue anyway.
        pass


def _extract_chosen_select_id(step):
    raw_candidates = [
        _clean_text(step.get("id")),
        _clean_text(step.get("selector")),
    ]

    for raw_value in raw_candidates:
        if not raw_value:
            continue
        match = re.search(r"([A-Za-z0-9_]+)_(?:chzn|chosen)_o_\d+", raw_value, re.IGNORECASE)
        if match:
            return match.group(1)

    return ""


def _is_chosen_search_input(step):
    if step.get("type") != "input" or step.get("tag") != "input":
        return False

    selector = _clean_text(step.get("selector"))
    if "_chzn" not in selector.lower() and "_chosen" not in selector.lower():
        return False

    return not any(
        _clean_text(step.get(key))
        for key in ("id", "name", "label", "value", "text", "placeholder", "aria_label")
    )


def _normalize_recorded_step(step):
    if not isinstance(step, dict):
        return None

    normalized = {
        "type": _safe_lower(step.get("type")),
        "tag": _safe_lower(step.get("tag")),
        "id": _clean_text(step.get("id")),
        "name": _clean_text(step.get("name")),
        "label": _clean_text(step.get("label")),
        "text": _clean_text(step.get("text")),
        "value": _clean_text(step.get("value")),
        "selector": _clean_text(step.get("selector")),
        "role": _safe_lower(step.get("role")),
        "placeholder": _clean_text(step.get("placeholder")),
        "aria_label": _clean_text(step.get("aria_label")),
        "input_type": _safe_lower(step.get("input_type")),
    }

    if not normalized["type"]:
        return None

    if _is_chosen_search_input(normalized):
        return None

    if normalized["tag"] == "a" and normalized["type"] == "click":
        normalized["type"] = "click_link"

    chosen_select_id = _extract_chosen_select_id(normalized)
    if (
        chosen_select_id
        and normalized["type"] in {"click", "click_link"}
        and normalized.get("text")
    ):
        normalized["type"] = "select"
        normalized["tag"] = "select"
        normalized["id"] = chosen_select_id
        normalized["selector"] = _css_attr_selector("id", chosen_select_id, tag="select")
        normalized["value"] = normalized.get("text", "")
        normalized["input_type"] = "select-one"

    if normalized["type"] == "input" and normalized.get("input_type") in {"checkbox", "radio", "button", "submit"}:
        normalized["type"] = "click"
        if not normalized.get("text"):
            normalized["text"] = normalized.get("value") or normalized.get("label") or ""

    if normalized["type"] in {"input", "select"} and not normalized["label"]:
        normalized["label"] = normalized["aria_label"] or normalized["placeholder"]

    if normalized["type"] in {"click", "click_link"} and not normalized["text"]:
        normalized["text"] = normalized["label"] or normalized["aria_label"]

    return {key: value for key, value in normalized.items() if value not in ("", None)}


def _same_step_target(left_step, right_step):
    identity_keys = (
        "type",
        "tag",
        "id",
        "name",
        "label",
        "selector",
        "role",
        "placeholder",
        "aria_label",
        "input_type",
    )
    return all(left_step.get(key, "") == right_step.get(key, "") for key in identity_keys)


def _same_element_target(left_step, right_step):
    identity_keys = (
        "tag",
        "id",
        "name",
        "label",
        "selector",
        "role",
        "placeholder",
        "aria_label",
        "input_type",
    )
    return all(left_step.get(key, "") == right_step.get(key, "") for key in identity_keys)


def _is_text_entry_step(step):
    return step.get("type") in {"input", "select"} and step.get("tag") in {"input", "textarea", "select"}


def _is_noise_container_click(step):
    if step.get("type") not in {"click", "click_link"}:
        return False

    tag = step.get("tag", "")
    text = step.get("text", "")
    has_identity = any(step.get(key) for key in ("id", "name", "role", "aria_label", "label"))

    if has_identity:
        return False

    if _normalize_text(text) in {"citystate", "mailingaddress"}:
        return True

    return tag in {"div", "p", "h1", "h2", "h3", "h4", "h5"} and len(text) > 40


def _should_drop_step(current_step, next_step):
    if not next_step:
        return False

    current_type = current_step.get("type")
    next_type = next_step.get("type")
    current_tag = current_step.get("tag", "")
    current_input_type = current_step.get("input_type", "")

    if _is_noise_container_click(current_step):
        return True

    if current_step == next_step:
        return True

    # Clicking into a text field right before its recorded input/select value is noise.
    if current_type in {"click", "click_link"} and next_type in {"input", "select"}:
        if _same_element_target(current_step, next_step):
            if current_tag in {"input", "textarea", "select"} and current_input_type not in {"checkbox", "radio", "button", "submit"}:
                return True

    return False


def _normalize_workflow_steps(steps):
    raw_normalized_steps = []

    for raw_step in steps or []:
        step = _normalize_recorded_step(raw_step)
        if not step:
            continue

        if step["type"] in {"input", "select"} and not any(
            step.get(key) for key in ("id", "name", "label", "placeholder", "aria_label", "selector")
        ):
            continue

        if step["type"] in {"click", "click_link"} and not any(
            step.get(key) for key in ("id", "name", "label", "text", "aria_label", "selector")
        ):
            continue

        raw_normalized_steps.append(step)

    normalized_steps = []
    total_steps = len(raw_normalized_steps)

    for index, step in enumerate(raw_normalized_steps):
        next_step = raw_normalized_steps[index + 1] if index + 1 < total_steps else None

        if _should_drop_step(step, next_step):
            continue

        if (
            normalized_steps
            and step.get("type") == "input"
            and step.get("input_type") in {"checkbox", "radio", "button", "submit"}
            and normalized_steps[-1].get("type") in {"click", "click_link"}
            and _same_element_target(normalized_steps[-1], step)
        ):
            continue

        if normalized_steps and step["type"] in {"input", "select"} and _same_step_target(step, normalized_steps[-1]):
            normalized_steps[-1] = step
            continue

        if normalized_steps and step == normalized_steps[-1]:
            continue

        normalized_steps.append(step)

    return normalized_steps


def _find_elements_by_label(driver, text, exact=False):
    """Find form controls associated with a label that matches text."""
    if not text:
        return []

    norm = text.strip()
    if not norm:
        return []

    if exact:
        label_xpath = f"//label[normalize-space()='{norm}']"
    else:
        label_xpath = f"//label[contains(normalize-space(), '{norm}') ]"

    elements = []
    try:
        labels = driver.find_elements(By.XPATH, label_xpath)
    except WebDriverException:
        return []

    for label in labels:
        try:
            for_attr = label.get_attribute("for")
            if for_attr:
                for_el = driver.find_elements(By.ID, for_attr)
                for el in for_el:
                    if el.tag_name.lower() in {"input", "textarea", "select"}:
                        elements.append(el)

            nested = label.find_elements(By.XPATH, ".//input|.//textarea|.//select")
            elements.extend(nested)
        except WebDriverException:
            continue

    return elements


def _build_input_locators(step):
    """Build a list of Selenium locator strategies for input/select fields."""
    locators = []
    seen = set()

    label = step.get("label", "")
    placeholder = step.get("placeholder", "")
    aria_label = step.get("aria_label", "")
    id_attr = step.get("id", "")
    name = step.get("name", "")
    tag = _safe_lower(step.get("tag", ""))
    selector = step.get("selector", "")

    def _add(kind, **kwargs):
        key = (kind,) + tuple(sorted(kwargs.items()))
        if key in seen:
            return
        seen.add(key)
        locators.append({"kind": kind, **kwargs})

    for candidate in _candidate_values(label, aria_label):
        _add("label_exact", text=candidate)
        _add("label_contains", text=candidate)

    for candidate in _candidate_values(placeholder, label):
        _add("placeholder_exact", text=candidate)
        _add("placeholder_contains", text=candidate)

    if aria_label:
        selector_key = _css_attr_selector("aria-label", aria_label, tag=tag or None)
        if selector_key:
            _add("css", selector=selector_key)

    if id_attr:
        _add("id", value=id_attr)

    if name:
        _add("name", value=name)
        if tag:
            selector_key = _css_attr_selector("name", name, tag=tag)
            if selector_key:
                _add("css", selector=selector_key)

    if selector:
        _add("css", selector=selector)

    return locators


def _build_click_locators(step, include_structural_fallback=True):
    """Build Selenium locator strategies for clickable elements."""
    locators = []
    seen = set()

    step_type = _safe_lower(step.get("type", ""))
    tag = _safe_lower(step.get("tag", ""))
    text = step.get("text", "")
    label = step.get("label", "")
    aria_label = step.get("aria_label", "")
    id_attr = step.get("id", "")
    name = step.get("name", "")
    selector = step.get("selector", "")
    xpath = step.get("xpath", "")

    candidate_names = _candidate_values(text, label, aria_label)
    generic_choice_tokens = {"yes", "no", "true", "false"}
    prefer_structural_first = include_structural_fallback and (
        tag == "label" or _normalize_text(text) in generic_choice_tokens
    )

    def _add(kind, **kwargs):
        key = (kind,) + tuple(sorted(kwargs.items()))
        if key in seen:
            return
        seen.add(key)
        locators.append({"kind": kind, **kwargs})

    if prefer_structural_first:
        if id_attr:
            _add("id", value=id_attr)
        if name:
            _add("name", value=name)
        if selector:
            _add("css", selector=selector)
        if xpath:
            _add("xpath", selector=xpath)

    for candidate in candidate_names:
        normalized_candidate = _normalize_text(candidate)
        # Exclude internal boolean value attributes from being used as DOM text locators
        if normalized_candidate in {"true", "false", "on", "off"}:
            continue

        if tag:
            _add("tag_text_exact", tag=tag, text=candidate)
            _add("tag_text_contains", tag=tag, text=candidate)

        # Label-based
        _add("label_exact", text=candidate)
        _add("label_contains", text=candidate)

        # Generic clickable elements
        _add("button_text_exact", text=candidate)
        _add("button_text_contains", text=candidate)
        _add("input_value_exact", text=candidate)
        _add("input_value_contains", text=candidate)
        _add("link_text_exact", text=candidate)
        _add("link_text_contains", text=candidate)
        _add("generic_click_text", text=candidate)

    if include_structural_fallback:
        if not prefer_structural_first:
            if id_attr:
                _add("id", value=id_attr)
            if name:
                _add("name", value=name)
            if selector:
                _add("css", selector=selector)
            if xpath:
                _add("xpath", selector=xpath)

    # Special handling for submit buttons and links when we have direct text.
    if text:
        if step_type == "click":
            _add("css_with_text", selector="button, input[type='submit'], input[type='button']", text=text)
        elif step_type == "click_link" or tag == "a":
            _add("css_with_text", selector="a", text=text)

    return locators


def _find_elements_for_strategy(driver, strategy):
    """Resolve a locator strategy into a list of Selenium WebElements."""
    kind = strategy.get("kind")

    try:
        if kind == "id":
            return driver.find_elements(By.ID, strategy["value"])
        if kind == "name":
            return driver.find_elements(By.NAME, strategy["value"])
        if kind == "css":
            return driver.find_elements(By.CSS_SELECTOR, strategy["selector"])
        if kind == "xpath":
            return driver.find_elements(By.XPATH, strategy["selector"])
        if kind == "placeholder_exact":
            text = strategy.get("text", "")
            return driver.find_elements(By.XPATH, f"//*[@placeholder='{text}']")
        if kind == "placeholder_contains":
            text = strategy.get("text", "")
            return driver.find_elements(By.XPATH, f"//*[contains(@placeholder, '{text}')]")
        if kind == "label_exact":
            return _find_elements_by_label(driver, strategy.get("text", ""), exact=True)
        if kind == "label_contains":
            return _find_elements_by_label(driver, strategy.get("text", ""), exact=False)
        if kind == "tag_text_exact":
            tag = strategy.get("tag", "")
            text = strategy.get("text", "")
            if not tag or not text:
                return []
            return driver.find_elements(By.XPATH, f"//{tag}[normalize-space()='{text}']")
        if kind == "tag_text_contains":
            tag = strategy.get("tag", "")
            text = strategy.get("text", "")
            if not tag or not text:
                return []
            return driver.find_elements(By.XPATH, f"//{tag}[contains(normalize-space(), '{text}')]")
        if kind == "button_text_exact":
            text = strategy.get("text", "")
            return driver.find_elements(By.XPATH, f"//button[normalize-space()='{text}']")
        if kind == "button_text_contains":
            text = strategy.get("text", "")
            return driver.find_elements(By.XPATH, f"//button[contains(normalize-space(), '{text}')]")
        if kind == "input_value_exact":
            text = strategy.get("text", "")
            return driver.find_elements(
                By.XPATH,
                f"//input[(translate(@type, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='button' or translate(@type, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='submit') and normalize-space(@value)='{text}']",
            )
        if kind == "input_value_contains":
            text = strategy.get("text", "")
            return driver.find_elements(
                By.XPATH,
                f"//input[(translate(@type, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='button' or translate(@type, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='submit') and contains(normalize-space(@value), '{text}')]",
            )
        if kind == "link_text_exact":
            text = strategy.get("text", "")
            return driver.find_elements(By.XPATH, f"//a[normalize-space()='{text}']")
        if kind == "link_text_contains":
            text = strategy.get("text", "")
            return driver.find_elements(By.XPATH, f"//a[contains(normalize-space(), '{text}')]")
        if kind == "generic_click_text":
            text = strategy.get("text", "")
            xpath = (
                "//*[self::button or self::a or self::span or self::div or self::label]"
                f"[contains(normalize-space(), '{text}')]"
            )
            return driver.find_elements(By.XPATH, xpath)
        if kind == "css_with_text":
            selector = strategy.get("selector", "")
            text = strategy.get("text", "")
            if not selector:
                return []
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            return [el for el in elements if text and (text in (el.text or ""))]
    except WebDriverException:
        return []

    return []


def _generate_ssn():
    """Generate a syntactically valid SSN.

    Uses Faker when available, but always normalizes to 9 digits (no dashes).
    Falls back to a simple rule-based generator if Faker is missing.
    """
    def _is_valid_ssn_digits(candidate):
        if len(candidate) != 9 or not candidate.isdigit():
            return False

        area = int(candidate[0:3])
        group = int(candidate[3:5])
        serial = int(candidate[5:9])

        if area == 0 or area == 666 or area >= 900:
            return False
        if group == 0 or serial == 0:
            return False
        return True

    digits = None
    if fake is not None:
        try:
            s = fake.ssn()
            d = "".join(ch for ch in s if ch.isdigit())
            if _is_valid_ssn_digits(d):
                digits = d
        except Exception:
            pass

    if digits is None:
        # Fallback: basic SSN rules (no 000/666/900-999 areas, no all-zero groups)
        while True:
            area = random.randint(1, 899)
            if area == 666:
                continue
            group = random.randint(1, 99)
            serial = random.randint(1, 9999)
            digits = f"{area:03d}{group:02d}{serial:04d}"
            if _is_valid_ssn_digits(digits):
                break

    # Always return XXX-XX-XXXX format so masked inputs accept it directly
    return f"{digits[0:3]}-{digits[3:5]}-{digits[5:9]}"


def _generate_phone():
    return f"567{random.randint(200, 999)}{random.randint(1000, 9999)}"


def _parse_date_value(value):
    raw_value = _clean_text(value)
    if not raw_value:
        return None

    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw_value, fmt).date()
        except ValueError:
            continue

    return None


def _lookup_override_value(override_data_normalized, aliases):
    normalized_aliases = [_normalize_text(alias) for alias in aliases]
    for key, value in (override_data_normalized or {}).items():
        normalized_key = _normalize_text(key)
        if any(alias and (alias == normalized_key or alias in normalized_key) for alias in normalized_aliases):
            cleaned = _clean_text(value)
            if cleaned:
                return cleaned
    return ""


def _canonical_rule_name(value):
    normalized = _normalize_text(value)
    rule_aliases = {
        "first_name": ["firstname", "givenname"],
        "last_name": ["lastname", "surname", "familyname"],
        "gender": ["gender", "sex"],
        "marital": ["maritalstatus", "marital"],
        "dob": ["dob", "dateofbirth", "birthdate"],
        "effective_date": ["effectivedate", "effective", "coverageeffectivedate"],
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
        "address1": ["address1", "addressline1", "streetaddress", "street", "homeaddressaddress1"],
        "zip": ["zip", "zipcode", "postalcode", "postcode"],
        "county": ["county"],
        "city": ["city"],
        "state": ["state", "province"],
        "billing_location": ["billinglocation", "billing", "subgroup", "subgroupid"],
        "employee_class": ["employeeclass", "classid", "employeetype", "class"],
        "hire_date": ["dateofhire", "hiredate", "datehired", "employmentdate", "startdate"],
    }

    for canonical_name, aliases in rule_aliases.items():
        if any(alias == normalized or alias in normalized for alias in aliases):
            return canonical_name

    return ""


def _parse_fixed_rules_text(rules_text):
    directives = {}
    for raw_line in str(rules_text or "").splitlines():
        line = raw_line.strip().lstrip("-*0123456789. ").strip()
        if not line:
            continue

        if "->" in line:
            raw_key, raw_value = line.split("->", 1)
        elif ":" in line:
            raw_key, raw_value = line.split(":", 1)
        else:
            raw_key, raw_value = line, ""

        canonical_name = _canonical_rule_name(raw_key)
        if canonical_name:
            directives[canonical_name] = _clean_text(raw_value)

    return directives


def _lookup_zip_profile(zip_code):
    digits = "".join(ch for ch in str(zip_code or "") if ch.isdigit())[:5]
    if len(digits) != 5:
        digits = DEFAULT_VALID_ZIP

    location = ZIP_LOCATION_DATA.get(digits, {})
    return {
        "zip": digits,
        "county": location.get("county", ""),
        "city": location.get("city", ""),
        "state": location.get("state", ""),
        "billing_location": location.get("billing_location", ""),
        "employee_class": location.get("employee_class", ""),
    }


def _coerce_tobacco_value(value):
    normalized = _normalize_text(value)
    if normalized in {"true", "yes", "y", "1"}:
        return "true"
    if normalized in {"false", "no", "n", "0"}:
        return "false"
    return ""


def _minimum_hire_date(dob_date, minimum_years_after=15):
    import calendar as _cal

    min_year = dob_date.year + max(1, int(minimum_years_after))
    safe_day = min(dob_date.day, _cal.monthrange(min_year, dob_date.month)[1])
    return date(min_year, dob_date.month, safe_day)


def _coerce_hire_date(dob_date, requested_hire_date=None):
    minimum_hire_date = _minimum_hire_date(dob_date, minimum_years_after=15)
    today = date.today()

    if requested_hire_date is None:
        return minimum_hire_date, minimum_hire_date, today

    if requested_hire_date < minimum_hire_date:
        return minimum_hire_date, minimum_hire_date, today

    if requested_hire_date > today:
        return (
            today if today >= minimum_hire_date else minimum_hire_date,
            minimum_hire_date,
            today,
        )

    return requested_hire_date, minimum_hire_date, today


def _generate_profile(override_data_normalized=None, fixed_rules_text=""):
    """Generate one correlated fake person for a full automation run."""
    import calendar as _cal

    directives = _parse_fixed_rules_text(fixed_rules_text)

    zip_override = _lookup_override_value(
        override_data_normalized,
        ["zip", "zipcode", "postal code", "postcode"],
    )
    zip_from_rules = directives.get("zip", "")
    zip_profile = _lookup_zip_profile(zip_override or zip_from_rules or DEFAULT_VALID_ZIP)

    gender_override = _lookup_override_value(override_data_normalized, ["gender", "sex"])
    gender_rule = _clean_text(directives.get("gender", ""))
    gender = gender_override or (gender_rule if gender_rule.lower() in {"male", "female"} else "")
    if gender.lower() not in {"male", "female"}:
        gender = random.choice(["Male", "Female"])
    else:
        gender = gender.title()

    explicit_first = _lookup_override_value(
        override_data_normalized,
        ["first name", "firstname", "given name"],
    )
    explicit_last = _lookup_override_value(
        override_data_normalized,
        ["last name", "lastname", "surname"],
    )
    explicit_address1 = _lookup_override_value(
        override_data_normalized,
        ["address1", "address 1", "street address", "address"],
    )

    if explicit_first:
        first = explicit_first
    elif fake is not None:
        first = fake.first_name_male() if gender == "Male" else fake.first_name_female()
    else:
        first = "John" if gender == "Male" else "Jane"

    if explicit_last:
        last = explicit_last
    elif fake is not None:
        last = fake.last_name()
    else:
        last = "Doe"

    if explicit_address1:
        addr1 = explicit_address1
    elif fake is not None:
        addr1 = fake.street_address()
    else:
        addr1 = f"{random.randint(100, 9999)} Main St"

    explicit_dob = _parse_date_value(
        _lookup_override_value(
            override_data_normalized,
            ["dob", "date of birth", "birth date"],
        )
    )
    dob_cutoff_year = 1994
    year_match = re.search(r"(\d{4})", directives.get("dob", ""))
    if year_match:
        dob_cutoff_year = max(1900, int(year_match.group(1)) - 1)

    if explicit_dob is not None:
        dob_date = explicit_dob
    else:
        dob_year = random.randint(1930, dob_cutoff_year)
        dob_month = random.randint(1, 12)
        dob_day = random.randint(1, _cal.monthrange(dob_year, dob_month)[1])
        dob_date = date(dob_year, dob_month, dob_day)

    explicit_hire = _parse_date_value(
        _lookup_override_value(
            override_data_normalized,
            ["date of hire", "hire date", "employment date", "start date"],
        )
    )
    coerced_hire, minimum_hire_date, latest_hire_date = _coerce_hire_date(dob_date, explicit_hire)
    if explicit_hire is not None:
        hire_date = coerced_hire
    else:
        hire_window_days = max(0, (latest_hire_date - minimum_hire_date).days)
        hire_date = minimum_hire_date + timedelta(days=random.randint(0, hire_window_days))

    explicit_effective = _parse_date_value(
        _lookup_override_value(
            override_data_normalized,
            ["effective date", "effective", "coverage effective date"],
        )
    )
    effective_date = explicit_effective or date(date.today().year, 1, 1)

    ssn_value = _lookup_override_value(
        override_data_normalized,
        ["ssn", "social security", "social security number"],
    ) or _generate_ssn()
    phone_value = _lookup_override_value(
        override_data_normalized,
        ["cell phone", "phone", "mobile", "cell"],
    ) or _generate_phone()
    marital_value = _lookup_override_value(
        override_data_normalized,
        ["marital status", "marital"],
    ) or _clean_text(directives.get("marital", "")) or "Single"
    tobacco_value = _lookup_override_value(
        override_data_normalized,
        ["tobacco", "smoker", "smoking", "tobaco"],
    )
    tobacco_value = _coerce_tobacco_value(tobacco_value or directives.get("tobacco", "") or "false") or "false"

    county_value = _lookup_override_value(override_data_normalized, ["county"]) or zip_profile["county"]
    city_value = _lookup_override_value(override_data_normalized, ["city"]) or zip_profile["city"]
    state_value = _lookup_override_value(
        override_data_normalized,
        ["state", "province"],
    ) or zip_profile["state"]
    billing_location = _lookup_override_value(
        override_data_normalized,
        ["billing location", "billing", "subgroup", "subgroup id"],
    ) or zip_profile["billing_location"]
    employee_class = _lookup_override_value(
        override_data_normalized,
        ["employee class", "class id", "employee type"],
    ) or zip_profile["employee_class"]

    return {
        "gender": gender,
        "first_name": first,
        "last_name": last,
        "ssn": ssn_value,
        "phone": phone_value,
        "cell_phone": phone_value,
        "address1": addr1,
        "zip": zip_profile["zip"],
        "county": county_value,
        "city": city_value,
        "state": state_value,
        "billing_location": billing_location,
        "employee_class": employee_class,
        "marital": marital_value.title(),
        "tobacco": tobacco_value,
        "tobacco_label": "Yes" if tobacco_value == "true" else "No",
        "dob_date": dob_date.strftime("%m/%d/%Y"),
        "dob_year": str(dob_date.year),
        "dob_month": f"{dob_date.month:02d}",
        "dob_month_index": str(dob_date.month - 1),
        "dob_day": str(dob_date.day),
        "hire_date": hire_date.strftime("%m/%d/%Y"),
        "hire_year": str(hire_date.year),
        "hire_month": f"{hire_date.month:02d}",
        "hire_month_index": str(hire_date.month - 1),
        "hire_day": str(hire_date.day),
        "effective_date": effective_date.strftime("%m/%d/%Y"),
        "effective_year": str(effective_date.year),
        "effective_month": f"{effective_date.month:02d}",
        "effective_month_index": str(effective_date.month - 1),
        "effective_day": str(effective_date.day),
    }


def _identify_profile_field(label="", name="", id_attr="", text="", selector=""):
    combined = _normalize_text(" ".join([label, name, id_attr, text, selector]))

    if "dateofbirth" in combined or "birthdate" in combined or "dob" in combined:
        return "dob_date"
    if "dateofhire" in combined or "hiredate" in combined or "datehired" in combined or "employmentdate" in combined or "startdate" in combined:
        return "hire_date"
    if "effectivedate" in combined or "coverageeffectivedate" in combined:
        return "effective_date"
    if "firstname" in combined or "givenname" in combined:
        return "first_name"
    if "lastname" in combined or "surname" in combined or "familyname" in combined:
        return "last_name"
    if "socialsecurity" in combined or "socialsecuritynumber" in combined or "ssn" in combined:
        return "ssn"
    if "cellphone" in combined or "mobilephone" in combined or "altphone" in combined or "phone" in combined or "mobile" in combined or "cell" in combined:
        return "phone"
    if "address1" in combined or "addressline1" in combined or "streetaddress" in combined or "homeaddressaddress1" in combined:
        return "address1"
    if "zipcode" in combined or "postalcode" in combined or "postcode" in combined or "zip" in combined:
        return "zip"
    if "county" in combined:
        return "county"
    if "city" in combined:
        return "city"
    if "state" in combined or "province" in combined:
        return "state"
    if "maritalstatus" in combined or ("marital" in combined and "status" in combined):
        return "marital"
    if "gender" in combined or combined.endswith("sex"):
        return "gender"
    if "tobacco" in combined or "tobaco" in combined or "smoker" in combined or "smoking" in combined:
        return "tobacco"
    if "billinglocation" in combined or "subgroupid" in combined:
        return "billing_location"
    if "employeeclass" in combined or "classid" in combined:
        return "employee_class"

    return ""


def _get_profile_value_for_field(field_name, execution_profile, step_type="input", tag_name=""):
    if not field_name:
        return None

    value = execution_profile.get(field_name)
    if value in (None, ""):
        return None

    if field_name == "phone":
        return execution_profile.get("cell_phone") or value

    if field_name == "tobacco":
        if step_type == "select" or tag_name == "label":
            return execution_profile.get("tobacco_label") or ("Yes" if str(value).lower() == "true" else "No")
        if tag_name in {"li", "span", "a", "div"}:
            return execution_profile.get("tobacco_label") or ("Yes" if str(value).lower() == "true" else "No")

    return str(value)


def _get_profile_datepicker_value(step, execution_profile, calendar_context):
    if calendar_context not in {"dob", "hire", "effective"}:
        return None

    selector = step.get("selector", "")
    if step.get("type") == "select":
        if "select:nth-of-type(2)" in selector:
            return execution_profile.get(f"{calendar_context}_year")
        if "select:nth-of-type(1)" in selector:
            return execution_profile.get(f"{calendar_context}_month_index")
    if step.get("type") in {"click", "click_link"}:
        return execution_profile.get(f"{calendar_context}_day")

    return None


def _get_date_context_from_profile_field(profile_field):
    if profile_field == "dob_date":
        return "dob"
    if profile_field == "hire_date":
        return "hire"
    if profile_field == "effective_date":
        return "effective"
    return None


def _step_target_blob(step):
    return _normalize_text(
        " ".join(str(step.get(key, "")) for key in ("id", "name", "label", "selector", "text", "value"))
    )


def _parse_checkbox_state_token(value):
    token = _normalize_text(value)
    if token in {"true", "1", "checked"}:
        return True
    if token in {"false", "0", "unchecked"}:
        return False
    return None


def _infer_checkbox_target_state(step, resolved_text=""):
    # Only infer explicit state from value/text-like fields; labels like "No"
    # can represent a distinct radio option and should not be coerced to False.
    for candidate in (
        step.get("value", ""),
        resolved_text,
        step.get("text", ""),
    ):
        parsed = _parse_checkbox_state_token(candidate)
        if parsed is not None:
            return parsed
    return None


def _get_replay_target_key(step, profile_field=""):
    if step.get("type") not in {"input", "select"}:
        return None

    selector = _clean_text(step.get("selector"))
    if "ui-datepicker-div" in selector.lower():
        return None

    key_parts = [
        step.get("type", ""),
        step.get("tag", ""),
        step.get("id", ""),
        step.get("name", ""),
        selector,
        profile_field or "",
    ]
    normalized_parts = tuple(_normalize_text(part) for part in key_parts)
    if any(normalized_parts[2:5]):
        return normalized_parts
    return None


def _should_upgrade_click_to_input(step, tag_name, input_type, id_attr, name, label="", placeholder=""):
    if step.get("type") not in {"click", "click_link"}:
        return False

    if tag_name not in {"input", "textarea"}:
        return False

    if input_type in {"checkbox", "radio", "button", "submit", "file", "hidden"}:
        return False

    combined = f"{id_attr.lower()} {name.lower()}"
    if any(token in combined for token in ["btn", "submit", "login", "cancel", "sameas", "tobaco"]):
        return False

    date_blob = f"{id_attr.lower()} {name.lower()} {label.lower()} {placeholder.lower()}"
    if any(token in date_blob for token in ["dob", "birth", "hire", "date", "calendar", "datepicker"]):
        return False

    return True


def _set_toggle_input(locator, desired_checked=True):
    """Set a checkbox/radio to the requested checked state using Selenium.

    The "locator" here is a Selenium WebElement.
    """
    element = locator
    driver = getattr(element, "_parent", None)
    if desired_checked is None:
        desired_checked = True
    desired_checked = bool(desired_checked)

    def _is_checked():
        try:
            return element.is_selected()
        except Exception:
            try:
                return bool(driver.execute_script("return !!arguments[0].checked;", element))
            except Exception:
                return False

    _prepare_element_for_interaction(element)

    try:
        if _is_checked() != desired_checked:
            element.click()
        return _is_checked() == desired_checked
    except Exception:
        pass

    if driver is not None:
        try:
            driver.execute_script(
                """
                var el = arguments[0];
                el.checked = !!arguments[1];
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('click', { bubbles: true }));
                """,
                element,
                desired_checked,
            )
            if _is_checked() == desired_checked:
                return True
        except Exception:
            pass

        try:
            toggled = driver.execute_script(
                """
                var el = arguments[0];
                var labels = [];
                if (el.labels && el.labels.length) {
                    labels = Array.from(el.labels);
                } else if (el.id) {
                    labels = Array.from(document.querySelectorAll('label[for="' + el.id.replace(/"/g, '\\"') + '"]'));
                }

                var label = labels.find(function(item) { return !!item; });
                if (!label) {
                    return false;
                }

                label.click();
                return !!el.checked;
                """,
                element,
            )
            if toggled == desired_checked:
                return True
        except Exception:
            return False

    return False


def _expand_requested_field_names(field_name):
    expanded = {field_name}
    if field_name == "dob":
        expanded.update({"dob_date", "dob_year", "dob_month", "dob_month_index", "dob_day"})
    elif field_name == "hire_date":
        expanded.update({"hire_date", "hire_year", "hire_month", "hire_month_index", "hire_day"})
    elif field_name == "effective_date":
        expanded.update({"effective_date", "effective_year", "effective_month", "effective_month_index", "effective_day"})
    elif field_name == "phone":
        expanded.update({"phone", "cell_phone"})
    return expanded


def _get_requested_execution_fields(override_data_normalized, fixed_rules_text):
    requested_fields = set()

    for field_name in _parse_fixed_rules_text(fixed_rules_text):
        requested_fields.update(_expand_requested_field_names(field_name))

    for key in (override_data_normalized or {}):
        canonical_name = _canonical_rule_name(key)
        if canonical_name:
            requested_fields.update(_expand_requested_field_names(canonical_name))

    return requested_fields


def _is_generated_data_field(label="", name="", id_attr="", text="", selector=""):
    if _identify_profile_field(label, name, id_attr, text, selector):
        return True

    combined = _normalize_text(" ".join([label, name, id_attr, text, selector]))
    generic_tokens = [
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
    ]
    return any(token in combined for token in generic_tokens)


def _dispatch_text_events(locator):
    element = locator
    driver = getattr(element, "_parent", None)
    if driver is None:
        return

    try:
        driver.execute_script(
            """
            var el = arguments[0];
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
            """,
            element,
        )
    except Exception:
        pass


def _prepare_element_for_interaction(locator):
    element = locator
    driver = getattr(element, "_parent", None)
    if driver is None:
        return

    try:
        driver.execute_script(
            """
            var el = arguments[0];
            if (!el) {
                return;
            }
            try {
                el.scrollIntoView({ block: 'center', inline: 'nearest' });
            } catch (e) {}
            try {
                el.focus();
            } catch (e) {}
            """,
            element,
        )
        time.sleep(0.15)
    except Exception:
        pass


def _infer_field_value(label, name, id_attr, original_value="", execution_profile=None):
    profile_field = _identify_profile_field(label, name, id_attr)
    if execution_profile and profile_field:
        profile_value = _get_profile_value_for_field(profile_field, execution_profile)
        if profile_value is not None:
            return profile_value

    combined = f"{label.lower()} {name.lower()} {id_attr.lower()}"

    if "email" in combined:
        if fake is not None:
            try:
                return fake.unique.email()
            except Exception:
                pass
        return f"user{random.randint(1000, 9999)}@example.com"

    if "first" in combined and "name" in combined:
        return fake.first_name() if fake is not None else "John"
    if "last" in combined and "name" in combined:
        return fake.last_name() if fake is not None else "Doe"
    if "middle" in combined and "name" in combined:
        return fake.first_name() if fake is not None else "A"

    if "ssn" in combined or "socialsecurity" in combined:
        return _generate_ssn()
    if "phone" in combined or "mobile" in combined or "cell" in combined:
        return _generate_phone()

    if "address" in combined and "email" not in combined:
        if "street" in combined or "addr1" in combined or "address1" in combined:
            return fake.street_address() if fake is not None else "123 Main St"
        if "addr2" in combined or "address2" in combined:
            return fake.secondary_address() if fake is not None else "Apt 1"
        return fake.address() if fake is not None else "123 Main St"
    if "city" in combined:
        return fake.city() if fake is not None else "Springfield"
    if "state" in combined or "province" in combined:
        return fake.state() if fake is not None else "CA"
    if "country" in combined:
        return fake.country() if fake is not None else "USA"
    if "zip" in combined or "postal" in combined or "postcode" in combined:
        return DEFAULT_VALID_ZIP

    if "dob" in combined or "dateofbirth" in combined or ("birth" in combined and "date" in combined):
        if fake is not None:
            return fake.date_of_birth(minimum_age=18, maximum_age=90).strftime("%m/%d/%Y")
        return "01/01/1980"
    if "date" in combined:
        if fake is not None:
            return fake.date_between(start_date="-5y", end_date="today").strftime("%m/%d/%Y")
        return "01/01/2020"
    if "age" in combined:
        return str(fake.random_int(min=18, max=90)) if fake is not None else str(random.randint(18, 90))

    if "company" in combined or "employer" in combined or "organisation" in combined or "organization" in combined:
        return fake.company() if fake is not None else "ACME Corp"
    if "salary" in combined or "income" in combined or "amount" in combined or "total" in combined:
        return str(fake.pyint(min_value=1000, max_value=100000)) if fake is not None else str(random.randint(1000, 100000))

    if original_value:
        return original_value

    return fake.word() if fake is not None else "value"


def _is_date_related_step(step):
    blob = " ".join(
        str(step.get(key, "")).lower()
        for key in ["label", "name", "id", "text", "selector", "value"]
    )
    return any(token in blob for token in ["dob", "dateofbirth", "birth", "date", "datepicker", "ui-datepicker", "hire"])


def _set_text_input(locator, value, prefer_typing=False):
    element = locator
    driver = getattr(element, "_parent", None)
    target = str(value)

    _prepare_element_for_interaction(element)

    def current_matches():
        try:
            current = element.get_attribute("value") or ""
            return _normalize_text(current) == _normalize_text(target)
        except Exception:
            return False

    def _do_type(text):
        try:
            element.send_keys(text)
            return True
        except Exception:
            return False

    if prefer_typing:
        try:
            element.click()
            try:
                element.send_keys(Keys.CONTROL + "a")
                element.send_keys(Keys.DELETE)
            except Exception:
                pass
            _do_type(target)
            _dispatch_text_events(element)
            try:
                element.send_keys(Keys.TAB)
            except Exception:
                pass
            if current_matches():
                return True
        except Exception:
            pass

    try:
        element.clear()
        element.send_keys(target)
        _dispatch_text_events(element)
        try:
            element.send_keys(Keys.TAB)
        except Exception:
            pass
        if current_matches():
            return True
    except Exception:
        pass

    try:
        element.click()
        try:
            element.send_keys(Keys.CONTROL + "a")
            element.send_keys(Keys.DELETE)
        except Exception:
            pass
        _do_type(target)
        _dispatch_text_events(element)
        try:
            element.send_keys(Keys.TAB)
        except Exception:
            pass
        return current_matches()
    except Exception:
        return False


def _refresh_select_widget_ui(element, target=None):
    driver = getattr(element, "_parent", None)
    if driver is None:
        return

    try:
        driver.execute_script(
            """
            var el = arguments[0];
            var rawTarget = arguments[1];
            var normalize = function(value) {
                return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
            };

            var selectedOption = null;
            if (el.options && el.options.length) {
                selectedOption = Array.from(el.options).find(function(option) {
                    return option.selected;
                }) || null;

                if (!selectedOption && rawTarget) {
                    var normalizedTarget = normalize(rawTarget);
                    selectedOption = Array.from(el.options).find(function(option) {
                        var optionValue = String(option.value || '');
                        var optionText = normalize(option.textContent || option.label || '');
                        return optionValue === rawTarget || (optionText && (optionText === normalizedTarget || optionText.includes(normalizedTarget) || normalizedTarget.includes(optionText)));
                    }) || null;
                }
            }

            var selectedText = '';
            if (selectedOption) {
                selectedText = String(selectedOption.textContent || selectedOption.label || '').trim();
            }

            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));

            if (window.jQuery) {
                var $select = window.jQuery(el);
                $select.trigger('change');
                $select.trigger('change.select2');
                $select.trigger('chosen:updated');
                $select.trigger('liszt:updated');

                if (typeof $select.select2 === 'function') {
                    $select.trigger('change.select2');
                }

                if (typeof $select.selectpicker === 'function') {
                    try {
                        $select.selectpicker('refresh');
                    } catch (e) {}
                }
            }

            if (el.id) {
                var chosenIds = [el.id + '_chzn', el.id + '_chosen'];
                for (var i = 0; i < chosenIds.length; i++) {
                    var chosenRoot = document.getElementById(chosenIds[i]);
                    if (!chosenRoot) {
                        continue;
                    }

                    var chosenSingle = chosenRoot.querySelector('.chzn-single, .chosen-single');
                    if (chosenSingle) {
                        chosenSingle.classList.remove('chzn-default', 'chosen-default');
                    }

                    var chosenDisplay = chosenRoot.querySelector('.chzn-single span, .chosen-single span, .chzn-single, .chosen-single');
                    if (chosenDisplay && selectedText) {
                        chosenDisplay.textContent = selectedText;
                    }
                }

                var select2Display = document.getElementById('select2-' + el.id + '-container');
                if (!select2Display) {
                    select2Display = document.querySelector('[aria-labelledby="select2-' + el.id + '-container"]');
                }
                if (select2Display && selectedText) {
                    select2Display.textContent = selectedText;
                    select2Display.setAttribute('title', selectedText);
                }
            }

            var bootstrapRoot = el.closest('.bootstrap-select') || (el.parentElement && el.parentElement.querySelector ? el.parentElement.querySelector('.bootstrap-select') : null);
            if (bootstrapRoot && selectedText) {
                var bootstrapDisplay = bootstrapRoot.querySelector('.filter-option-inner-inner, .filter-option');
                if (bootstrapDisplay) {
                    bootstrapDisplay.textContent = selectedText;
                }
            }
            """,
            element,
            None if target is None else str(target),
        )
    except Exception:
        pass


def _set_select_value(locator, value):
    element = locator
    driver = getattr(element, "_parent", None)
    target = str(value)
    normalized_target = _normalize_text(target)

    _prepare_element_for_interaction(element)

    def current_matches():
        try:
            select = Select(element)
            selected_options = select.all_selected_options
            if not selected_options:
                return False
            selected = selected_options[0]
            selected_value = selected.get_attribute("value") or ""
            # Use get_attribute("textContent") because element.text is empty for hidden dropdowns (like Chosen)
            selected_text = (selected.get_attribute("textContent") or selected.text or "").strip()
            selected_value_norm = _normalize_text(selected_value)
            selected_text_norm = _normalize_text(selected_text)
            if selected_value == target or selected_value_norm == normalized_target:
                return True
            if selected_text_norm and selected_text_norm == normalized_target:
                return True
            return False
        except Exception:
            return False

    element_id = "unknown"
    try:
        element_id = element.get_attribute("id")
    except Exception:
        pass
    print(f"DEBUG: _set_select_value called for element id='{element_id}' with target='{target}'")

    try:
        select = Select(element)
    except Exception as e:
        print(f"DEBUG: Select(element) failed: {e}")
        return False

    # Try value and visible text first.
    for mode in ("value", "text"):
        try:
            if mode == "value":
                select.select_by_value(target)
            else:
                select.select_by_visible_text(target)
            _refresh_select_widget_ui(element, target)
            _dispatch_text_events(element)
            if current_matches():
                print(f"DEBUG: Successfully set using native Select mode='{mode}'")
                return True
        except Exception as e:
            print(f"DEBUG: Native Select mode='{mode}' threw exception: {e}")
            continue

    # Fallback: use JS to match loosely on option text/value.
    print(f"DEBUG: Falling back to JS selection for '{target}'...")
    if driver is not None:
        try:
            updated = driver.execute_script(
                """
                var el = arguments[0];
                var rawTarget = arguments[1];
                var normalize = function(value) {
                    return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                };
                var target = normalize(rawTarget);
                var options = Array.from(el.options || []);
                var match = options.find(function(option) {
                    var optionValue = String(option.value || '');
                    var optionText = normalize(option.textContent || option.label || '');
                    return optionValue === rawTarget || (optionText && (optionText === target || optionText.includes(target) || target.includes(optionText)));
                });

                if (!match) {
                    return false;
                }

                el.value = match.value;
                for (var i = 0; i < options.length; i++) {
                    options[i].selected = options[i] === match;
                }

                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));

                if (window.jQuery) {
                    window.jQuery(el).trigger('change');
                    window.jQuery(el).trigger('change.select2');
                    window.jQuery(el).trigger('chosen:updated');
                    window.jQuery(el).trigger('liszt:updated');
                    if (typeof window.jQuery(el).selectpicker === 'function') {
                        try {
                            window.jQuery(el).selectpicker('refresh');
                        } catch (e) {}
                    }
                }

                if (el.id) {
                    var companionIds = [el.id + '_chzn', el.id + '_chosen'];
                    for (var j = 0; j < companionIds.length; j++) {
                        var root = document.getElementById(companionIds[j]);
                        if (!root) {
                            continue;
                        }

                        var displayNode = root.querySelector('.chzn-single span, .chosen-single span, .chzn-single, .chosen-single');
                        if (displayNode) {
                            displayNode.textContent = (match.textContent || match.label || '').trim();
                        }
                    }
                }

                return true;
                """,
                element,
                target,
            )
            print(f"DEBUG: JS executed, returned {updated}")
            if updated:
                _refresh_select_widget_ui(element, target)
                _dispatch_text_events(element)
                if current_matches():
                    print(f"DEBUG: JS selection succeeded and verified for '{target}'")
                    return True
                else:
                    print(f"DEBUG: JS reported success, but current_matches() failed for '{target}'. Proceeding anyway.")
                    return True
        except Exception as e:
            print(f"DEBUG: JS fallback threw exception: {e}")
            pass

    print(f"DEBUG: _set_select_value fully failed for '{target}'")
    return False


def _handle_datepicker_select(driver, step, value):
    selector = step.get("selector", "")
    target = str(value)

    try:
        WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "ui-datepicker-div"))
        )
    except TimeoutException:
        return False

    css_selectors = []
    if "select:nth-of-type(2)" in selector or (target.isdigit() and len(target) == 4):
        css_selectors.append("#ui-datepicker-div select.ui-datepicker-year")
        css_selectors.append("#ui-datepicker-div select:nth-of-type(2)")
    if "select:nth-of-type(1)" in selector:
        css_selectors.append("#ui-datepicker-div select.ui-datepicker-month")
        css_selectors.append("#ui-datepicker-div select:nth-of-type(1)")
    if selector:
        css_selectors.append(selector)

    for css in css_selectors:
        try:
            element = WebDriverWait(driver, 2).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, css))
            )
            if _set_select_value(element, target):
                time.sleep(0.5)
                return True
        except Exception:
            continue

    return False


def _handle_datepicker_day_click(driver, text):
    day_text = str(text).strip()
    if not day_text:
        return False

    try:
        WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "ui-datepicker-div"))
        )
    except TimeoutException:
        return False

    try:
        clicked = bool(
            driver.execute_script(
                """
                var targetText = arguments[0];
                var normalize = function(value) { return String(value || '').trim(); };
                var root = document.querySelector('#ui-datepicker-div');
                if (!root) { return false; }

                var candidates = Array.from(root.querySelectorAll('td:not(.ui-datepicker-other-month) a'))
                    .concat(Array.from(root.querySelectorAll('td:not(.ui-datepicker-other-month) button')))
                    .concat(Array.from(root.querySelectorAll('a, button')));

                var match = candidates.find(function(item) {
                    return normalize(item.textContent) === targetText;
                });

                if (!match) { return false; }
                match.scrollIntoView({ block: 'nearest' });
                match.click();
                return true;
                """,
                day_text,
            )
        )
        if clicked:
            time.sleep(0.5)
            return True
    except Exception:
        pass

    candidate_selectors = [
        (By.CSS_SELECTOR, "#ui-datepicker-div td:not(.ui-datepicker-other-month) a"),
        (By.CSS_SELECTOR, "#ui-datepicker-div td:not(.ui-datepicker-other-month) button"),
        (By.CSS_SELECTOR, "#ui-datepicker-div td a"),
    ]

    for by, css in candidate_selectors:
        try:
            elements = driver.find_elements(by, css)
            for el in elements:
                try:
                    if (el.text or "").strip() == day_text:
                        el.click()
                        time.sleep(0.5)
                        return True
                except Exception:
                    continue
        except Exception:
            continue

    return False


def _click_chosen_option(driver, container_id, target_text):
    if not container_id or not target_text:
        return False

    try:
        return bool(
            driver.execute_script(
                """
                var containerId = arguments[0];
                var targetText = arguments[1];
                var normalize = function(value) {
                    return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                };
                var root = document.getElementById(containerId);
                if (!root) { return false; }

                var backingSelectId = containerId.replace(/_chzn$/, '');
                var backingSelect = document.getElementById(backingSelectId);
                var target = normalize(targetText);

                if (backingSelect && backingSelect.options) {
                    var option = Array.from(backingSelect.options).find(function(item) {
                        var text = normalize(item.textContent || item.label || '');
                        return text && (text === target || text.includes(target) || target.includes(text));
                    });

                    if (option) {
                        backingSelect.value = option.value;
                        backingSelect.dispatchEvent(new Event('input', { bubbles: true }));
                        backingSelect.dispatchEvent(new Event('change', { bubbles: true }));

                        var selectedText = normalize(
                            backingSelect.selectedOptions && backingSelect.selectedOptions[0]
                                ? backingSelect.selectedOptions[0].textContent
                                : ''
                        );
                        if (selectedText && (selectedText === target || selectedText.includes(target) || target.includes(selectedText))) {
                            return true;
                        }
                    }
                }

                var trigger = root.querySelector('a, .chzn-single, .chosen-single');
                if (trigger) { trigger.click(); }

                var candidates = Array.from(root.querySelectorAll('li'))
                    .concat(Array.from(document.querySelectorAll('li.active-result, li.result-selected, .chzn-results li, .chosen-results li')));

                var match = candidates.find(function(item) {
                    var text = normalize(item.textContent);
                    return text && (text.includes(target) || target.includes(text));
                });

                if (!match) { return false; }

                match.scrollIntoView({ block: 'nearest' });
                match.click();

                var selectedNode = root.querySelector('.chzn-single span, .chosen-single span, .chzn-single, .chosen-single');
                var selectedText = normalize(
                    (selectedNode ? selectedNode.textContent : '') ||
                    (backingSelect && backingSelect.selectedOptions && backingSelect.selectedOptions[0] ? backingSelect.selectedOptions[0].textContent : '')
                );

                return !selectedText || selectedText === target || selectedText.includes(target) || target.includes(selectedText);
                """,
                container_id,
                str(target_text),
            )
        )
    except Exception:
        return False


def _click_close_action(driver, step):
    """Robustly click Close actions (summary/modal close links/buttons)."""
    text = _clean_text(step.get("text", ""))
    id_attr = _clean_text(step.get("id", ""))
    selector = _clean_text(step.get("selector", ""))
    xpath = _clean_text(step.get("xpath", ""))

    is_close_step = _normalize_text(text) == "close"
    if not is_close_step:
        return False

    # Prefer recorded structural locators first to avoid clicking unrelated "Close" labels.
    if id_attr:
        try:
            el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.ID, id_attr)))
            _prepare_element_for_interaction(el)
            el.click()
            return True
        except Exception:
            pass

    if selector:
        try:
            el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
            _prepare_element_for_interaction(el)
            el.click()
            return True
        except Exception:
            try:
                clicked = bool(
                    driver.execute_script(
                        """
                        var sel = arguments[0];
                        var el = document.querySelector(sel);
                        if (!el) return false;
                        el.scrollIntoView({block: 'center'});
                        el.click();
                        return true;
                        """,
                        selector,
                    )
                )
                if clicked:
                    return True
            except Exception:
                pass

    if xpath:
        try:
            el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            _prepare_element_for_interaction(el)
            el.click()
            return True
        except Exception:
            pass

    # Modal-specific fallback patterns.
    try:
        clicked = bool(
            driver.execute_script(
                """
                var candidates = [];
                candidates = candidates.concat(Array.from(document.querySelectorAll(
                    '#EnrollSummaryWrap a, a[title*="Close"], a[aria-label*="Close"], button[title*="Close"], button[aria-label*="Close"], .ui-dialog-titlebar-close'
                )));

                var norm = function(v) { return String(v || '').trim().toLowerCase(); };
                var match = candidates.find(function(el) {
                    var t = norm(el.textContent || el.innerText || el.value || '');
                    return t === 'close' || t.indexOf('close') >= 0;
                }) || candidates[0];

                if (!match) return false;
                match.scrollIntoView({block: 'center'});
                match.click();
                return true;
                """
            )
        )
        return clicked
    except Exception:
        return False


def _fill_remaining_visible_inputs(driver, override_data_normalized, execution_profile=None):
    """Best-effort auto-fill of any remaining visible text fields."""

    try:
        fields = driver.execute_script(
            """
            return Array.from(document.querySelectorAll('input, textarea')).map(function(el) {
                var type = (el.type || '').toLowerCase();
                var style = window.getComputedStyle(el);
                var visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) &&
                              style.visibility !== 'hidden' && style.display !== 'none';
                if (!visible) return null;
                if (el.disabled || el.readOnly) return null;
                if (['hidden', 'password', 'submit', 'button', 'checkbox', 'radio', 'file'].includes(type)) return null;
                var label = '';
                if (el.labels && el.labels.length) {
                    label = Array.from(el.labels).map(function(item) { return item.innerText || ''; }).join(' ').trim();
                }
                return {
                    id: el.id || '',
                    name: el.name || '',
                    type: type,
                    label: label,
                    value: el.value || ''
                };
            }).filter(Boolean);
            """
        )
    except Exception:
        return

    for field in fields:
        if str(field.get("value", "")).strip():
            continue

        label = field.get("label", "")
        name = field.get("name", "")
        id_attr = field.get("id", "")

        val_to_use = None
        label_c = _normalize_text(label)
        name_c = _normalize_text(name)
        id_c = _normalize_text(id_attr)

        for ok, ov in (override_data_normalized or {}).items():
            ok_c = _normalize_text(ok)
            if ok_c and (ok_c in label_c or ok_c in name_c or ok_c in id_c):
                val_to_use = str(ov)
                break

        if val_to_use is None:
            val_to_use = _infer_field_value(label, name, id_attr, execution_profile=execution_profile)

        step_like = {
            "label": label,
            "name": name,
            "id": id_attr,
            "placeholder": "",
            "aria_label": "",
            "tag": "input",
            "selector": "",
        }

        element = None
        for strategy in _build_input_locators(step_like):
            try:
                elements = _find_elements_for_strategy(driver, strategy)
                if elements:
                    element = elements[0]
                    break
            except Exception:
                continue

        if element is None:
            continue

        combined = f"{label.lower()} {name.lower()} {id_attr.lower()}"
        prefer_typing = any(
            token in combined
            for token in ["ssn", "phone", "mobile", "cell", "mask", "date", "dob", "zip", "postal", "postcode"]
        )
        if _set_text_input(element, val_to_use, prefer_typing=prefer_typing):
            print(f"Auto-filled remaining field '{label or name or id_attr}' with '{val_to_use}'")


WORKFLOW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflows")
os.makedirs(WORKFLOW_DIR, exist_ok=True)


def _get_workflow_path(name):
    if not name.endswith(".json"):
        name += ".json"
    return os.path.join(WORKFLOW_DIR, name)


def save_workflow(url, steps, name="workflow.json"):
    path = _get_workflow_path(name)
    existing_payload = {}

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as existing_file:
                loaded_payload = json.load(existing_file)
            if isinstance(loaded_payload, dict):
                existing_payload = loaded_payload
        except Exception:
            existing_payload = {}

    payload = {
        key: value
        for key, value in existing_payload.items()
        if key not in {"url", "steps", "schema_version", "recorded_at"}
    }
    payload.update(
        {
            "schema_version": WORKFLOW_SCHEMA_VERSION,
            "recorded_at": int(time.time()),
            "url": url,
            "steps": _normalize_workflow_steps(steps),
        }
    )

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4)


def compact_workflow(name="workflow.json"):
    path = _get_workflow_path(name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No workflow recorded as {name}. Please record a workflow first.")

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        raw_steps = payload
        updated_payload = {
            "schema_version": WORKFLOW_SCHEMA_VERSION,
            "url": None,
            "steps": _normalize_workflow_steps(raw_steps),
        }
    elif isinstance(payload, dict):
        raw_steps = payload.get("steps", [])
        updated_payload = dict(payload)
        updated_payload["schema_version"] = max(
            WORKFLOW_SCHEMA_VERSION, int(payload.get("schema_version", 1))
        )
        updated_payload["steps"] = _normalize_workflow_steps(raw_steps)
    else:
        raw_steps = []
        updated_payload = {
            "schema_version": WORKFLOW_SCHEMA_VERSION,
            "url": None,
            "steps": [],
        }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(updated_payload, f, indent=4)

    return {
        "path": path,
        "before_count": len(raw_steps or []),
        "after_count": len(updated_payload.get("steps", [])),
    }


def load_workflow(name="workflow.json"):
    path = _get_workflow_path(name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No workflow recorded as {name}. Please record a workflow first.")

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        return {
            "schema_version": 1,
            "url": None,
            "steps": _normalize_workflow_steps(payload),
        }

    if not isinstance(payload, dict):
        return {
            "schema_version": 1,
            "url": None,
            "steps": [],
        }

    loaded_payload = dict(payload)
    loaded_payload["schema_version"] = payload.get("schema_version", 1)
    loaded_payload["url"] = payload.get("url")
    loaded_payload["steps"] = _normalize_workflow_steps(payload.get("steps", []))
    return loaded_payload


def start_teaching_mode(url, workflow_name="workflow.json"):
    """Open a browser and capture user interactions using Selenium.

    Returns the list of normalized steps; saving is handled by the caller.
    """

    steps = []
    driver = _create_webdriver(headless=False)

    try:
        driver.get(url)
        _wait_for_page_ready(driver)

        injection_script = """
        if (!window.__recordingScriptInjected) {
            window.__recordingScriptInjected = true;
            window.__recordedInteractions = window.__recordedInteractions || [];
            
            // 1. Recover from window.name (Handles cross-domain navigation)
            try {
                if (window.name && window.name.indexOf('__REC_PAYLOAD:') === 0) {
                    var parts = window.name.split('|||');
                    var payloadStr = parts[0].substring('__REC_PAYLOAD:'.length);
                    var parsedWindowName = JSON.parse(payloadStr);
                    window.__recordedInteractions = parsedWindowName.concat(window.__recordedInteractions);
                    // Restore original window name
                    window.name = parts.slice(1).join('|||');
                }
            } catch(e) {}

            // 2. Recover from sessionStorage (Handles same-domain navigation securely)
            try {
                var stored = sessionStorage.getItem('__recordedInteractions');
                if (stored) {
                    var parsedSession = JSON.parse(stored);
                    window.__recordedInteractions = parsedSession.concat(window.__recordedInteractions);
                    sessionStorage.removeItem('__recordedInteractions');
                }
            } catch(e) {}

            window.addEventListener('beforeunload', function() {
                try {
                    if (window.__recordedInteractions && window.__recordedInteractions.length) {
                        var payload = JSON.stringify(window.__recordedInteractions);
                        sessionStorage.setItem('__recordedInteractions', payload);
                        window.name = '__REC_PAYLOAD:' + payload + '|||' + window.name;
                    }
                } catch(e) {}
            });

        function escapeSelector(value) {
            if (window.CSS && typeof window.CSS.escape === 'function') {
                return window.CSS.escape(String(value));
            }
            return String(value).replace(/([#.;?+*~':"!^$\\[\\]()=>|/@])/g, '\\\\$1');
        }

        function pushInteraction(payload) {
            try {
                window.__recordedInteractions.push(payload);
            } catch (e) {
                console.error('Unable to store interaction', e);
            }
        }

        function getCssPath(el) {
            if (!(el instanceof Element)) return '';
            const path = [];
            while (el && el.nodeType === Node.ELEMENT_NODE) {
                let selector = el.nodeName.toLowerCase();
                if (el.id) {
                    selector += '#' + escapeSelector(el.id);
                    path.unshift(selector);
                    break;
                }
                let sibling = el;
                let nth = 1;
                while ((sibling = sibling.previousElementSibling)) {
                    if (sibling.nodeName.toLowerCase() === selector) {
                        nth += 1;
                    }
                }
                selector += ':nth-of-type(' + nth + ')';
                path.unshift(selector);
                el = el.parentElement;
            }
            return path.join(' > ');
        }

        function getFallbackSelector(el) {
            if (!(el instanceof Element)) return '';
            if (el.id) return '#' + escapeSelector(el.id);

            const testAttrs = ['data-testid', 'data-test', 'data-qa'];
            for (const attr of testAttrs) {
                const value = el.getAttribute(attr);
                if (value) {
                    return '[' + attr + '=' + JSON.stringify(value) + ']';
                }
            }

            if (el.name) {
                return el.tagName.toLowerCase() + '[name=' + JSON.stringify(el.name) + ']';
            }

            return getCssPath(el);
        }

        function getSemanticLabel(el) {
            if (el.labels && el.labels.length > 0) return el.labels[0].innerText.trim();
            if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
            if (el.placeholder) return el.placeholder;
            return el.name || el.id || "";
        }

        function isDateLike(el) {
            if (!(el instanceof Element)) return false;
            const blob = [
                el.id || '',
                el.name || '',
                el.placeholder || '',
                el.getAttribute('aria-label') || '',
                el.getAttribute('title') || '',
                el.className || '',
                getSemanticLabel(el),
            ].join(' ').toLowerCase();
            return ['dob', 'birth', 'hire', 'date', 'calendar', 'datepicker'].some(token => blob.includes(token));
        }

        function buildChosenSelectPayload(el) {
            const optionNode = el.closest('li[id*="_chzn_o_"], li[id*="_chosen_o_"]');
            if (!optionNode || !optionNode.id) {
                return null;
            }

            const match = optionNode.id.match(/^(.*)_(?:chzn|chosen)_o_\d+$/i);
            if (!match) {
                return null;
            }

            const backingId = match[1];
            const backingSelect = document.getElementById(backingId);
            const optionText = (optionNode.innerText || optionNode.textContent || '').trim();
            if (!backingSelect || !optionText) {
                return null;
            }

            const normalize = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
            const matchedOption = Array.from(backingSelect.options || []).find(option => {
                const text = normalize(option.textContent || option.label || '');
                const target = normalize(optionText);
                return text && target && (text === target || text.includes(target) || target.includes(text));
            });

            return buildStepPayload(
                backingSelect,
                'select',
                {
                    value: matchedOption ? (matchedOption.value || optionText) : optionText,
                    text: optionText,
                }
            );
        }

        function buildStepPayload(el, type, extra) {
            extra = extra || {};
            const role = (el.getAttribute('role') || '').toLowerCase();
            return Object.assign({
                type: type,
                tag: el.tagName.toLowerCase(),
                id: el.id || '',
                name: el.name || '',
                label: getSemanticLabel(el),
                selector: getFallbackSelector(el),
                role: role,
                placeholder: el.placeholder || '',
                aria_label: el.getAttribute('aria-label') || '',
                input_type: (el.type || '').toLowerCase(),
            }, extra);
        }

        function captureValueInteraction(el) {
            if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement)) {
                return;
            }

            const tagName = el.tagName.toLowerCase();
            const inputType = (el.type || '').toLowerCase();
            if (tagName === 'input' && ['button', 'submit', 'checkbox', 'radio', 'file', 'hidden'].includes(inputType)) {
                return;
            }

            pushInteraction(buildStepPayload(
                el,
                tagName === 'select' ? 'select' : 'input',
                { value: el.value || '' }
            ));
        }

        document.addEventListener('change', function(e) {
            var el = e.target;
            captureValueInteraction(el);
        }, true);

        document.addEventListener('input', function(e) {
            var el = e.target;
            captureValueInteraction(el);
        }, true);

        document.addEventListener('blur', function(e) {
            var el = e.target;
            captureValueInteraction(el);
        }, true);

        document.addEventListener('click', function(e) {
            const chosenPayload = buildChosenSelectPayload(e.target);
            if (chosenPayload) {
                pushInteraction(chosenPayload);
                return;
            }

            const semanticTarget = e.target.closest(
                'button, a, label, img, .ui-datepicker-trigger, .ui-datepicker-prev, .ui-datepicker-next, .chzn-single, .chosen-single, [role="button"], [role="link"], [role="menuitem"], [role="tab"], [role="checkbox"], [role="radio"], [class*="calendar"], [class*="datepicker"], [title*="Calendar"], [title*="calendar"], [aria-label*="calendar"], [data-handler]'
            );
            const dateInputTarget = e.target.closest('input, textarea, select');
            const el = semanticTarget || (dateInputTarget && isDateLike(dateInputTarget) ? dateInputTarget : e.target);
            const text = (el.innerText || el.value || el.textContent || el.getAttribute('aria-label') || '').trim();

            if (!text && !el.id && !el.name && !el.getAttribute('aria-label')) {
                return;
            }

            pushInteraction(buildStepPayload(el, el.tagName === 'A' ? 'click_link' : 'click', { text: text }));
        }, true);
        }
        """

        driver.execute_script(injection_script)

        print(
            "Teaching mode active. Interact with the website. Click Stop Record in GUI or close browser when done."
        )

        stop_recording_event.clear()

        while not stop_recording_event.is_set():
            try:
                # Re-inject if navigated
                interactions = driver.execute_script(
                    "if (!window.__recordingScriptInjected) { return 'NEEDS_INJECTION'; }"
                    "var data = window.__recordedInteractions || []; window.__recordedInteractions = []; return data;"
                )
                if interactions == 'NEEDS_INJECTION':
                    driver.execute_script(injection_script)
                    interactions = driver.execute_script(
                        "var data = window.__recordedInteractions || []; window.__recordedInteractions = []; return data;"
                    )
            except WebDriverException:
                time.sleep(0.1)
                continue

            for interaction_data in interactions or []:
                normalized_step = _normalize_recorded_step(interaction_data)
                if not normalized_step:
                    continue
                    
                if steps and normalized_step["type"] in {"input", "select"} and _same_step_target(normalized_step, steps[-1]):
                    steps[-1] = normalized_step
                    print(f"Updated interaction: {normalized_step}")
                    continue
                    
                print(f"Captured interaction: {normalized_step}")
                steps.append(normalized_step)

            time.sleep(0.1)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"Teaching mode finished. Captured {len(steps)} steps.")
    return steps


def run_execution_mode(url=None, override_data=None, headless=False, workflow_name="workflow.json"):
    """Execute a recorded workflow using Selenium.

    override_data is a dict (e.g., {"SSN": "123-456", "First Name": "Alice"})
    that the LLM extracted from the user prompt.
    Returns (success: bool, message: str, screenshot_path: str | None, effective_data: dict).
    """

    if not override_data:
        override_data = {}

    try:
        workflow = load_workflow(workflow_name)
    except FileNotFoundError as exc:
        return False, str(exc), None, {}

    steps = workflow["steps"]
    target_url = url or workflow["url"]
    if not target_url:
        return (
            False,
            "No target URL found. Please provide a URL and record the workflow again.",
            None,
            {},
        )

    if not steps:
        return (
            False,
            "Recorded workflow is empty. Please record the workflow again.",
            None,
            {},
        )

    # Predefined User/Password blocks based on URL target.
    URL_CREDENTIALS = {}
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.properties")
    if os.path.exists(config_path):
        import configparser

        config = configparser.ConfigParser()
        config.read(config_path)
        for section in config.sections():
            URL_CREDENTIALS[section] = dict(config[section])

    override_data_normalized = {k.lower().strip(): v for k, v in override_data.items()}
    fixed_rules_text = workflow.get("fixed_rules", "")

    # Auto-merge credentials by URL substring
    for domain, creds in URL_CREDENTIALS.items():
        if domain in target_url:
            for c_key, c_val in creds.items():
                if c_key.lower() not in override_data_normalized:
                    override_data_normalized[c_key.lower()] = c_val

    execution_profile = _generate_profile(override_data_normalized, fixed_rules_text)
    requested_execution_fields = _get_requested_execution_fields(
        override_data_normalized, fixed_rules_text
    )
    restrict_generated_fields = bool(requested_execution_fields)
    effective_data = dict(execution_profile)
    effective_data.update({k: str(v) for k, v in override_data.items()})
    timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    screenshot_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "reports",
        f"success_{timestamp_str}.png",
    )
    os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)

    driver = _create_webdriver(headless=headless)

    try:
        driver.get(target_url)
        _wait_for_page_ready(driver)

        previous_step_was_date_related = False
        _last_calendar_type = None
        _calendar_open_count = 0
        _pending_direct_date_context = None
        completed_replay_targets = set()
        decline_all_clicked = False
        plan_decline_uncheck_count = 0
        plan_decline_checkbox_seen_count = 0
        
        stop_execution_event.clear()

        for step in steps:
            if stop_execution_event.is_set():
                print("Execution stopped by user.")
                return False, "Execution stopped by user.", None, {}
                
            step_blob = _step_target_blob(step)
            label = step.get("label", "").strip()
            name = step.get("name", "").strip()
            id_attr = step.get("id", "").strip()
            val_to_use = str(step.get("value", ""))
            text = step.get("text", "").strip()
            tag_name = step.get("tag", "").lower()
            selector = step.get("selector", "")
            role = step.get("role", "").strip().lower()
            placeholder = step.get("placeholder", "").strip()
            aria_label = step.get("aria_label", "").strip()
            input_type = step.get("input_type", "").strip().lower()
            current_step_is_date_related = _is_date_related_step(step)

            if previous_step_was_date_related:
                time.sleep(1.0)

            if "ssnmask" in step_blob:
                print(f"Skipping masked SSN field step: {step}")
                previous_step_was_date_related = current_step_is_date_related
                continue

            if _should_upgrade_click_to_input(
                step, tag_name, input_type, id_attr, name, label, placeholder
            ):
                step["type"] = "input"

            if step["type"] in ["input", "select"]:
                label_l, name_l, id_l = label.lower(), name.lower(), id_attr.lower()
                label_c = label_l.replace(" ", "").replace("_", "")
                name_c = name_l.replace(" ", "").replace("_", "").replace(".", "")
                id_c = id_l.replace(" ", "").replace("_", "")
                profile_field = _identify_profile_field(
                    label, name, id_attr, text, selector
                )
                replay_target_key = _get_replay_target_key(step, profile_field)
                explicit_override_applied = False
                is_datepicker_widget_step = (
                    current_step_is_date_related and "ui-datepicker-div" in selector
                )
                date_field_context = _get_date_context_from_profile_field(profile_field)

                if replay_target_key in completed_replay_targets:
                    print(
                        f"Skipping duplicate input/select step already completed: {label or name or id_attr or selector}"
                    )
                    previous_step_was_date_related = current_step_is_date_related
                    continue

                if current_step_is_date_related and _last_calendar_type in {"dob", "hire"}:
                    profile_field = f"{_last_calendar_type}_date"
                    date_field_context = _last_calendar_type

                for ok, ov in override_data_normalized.items():
                    ok_c = ok.replace(" ", "").replace("_", "").replace(".", "")
                    if ok_c in label_c or ok_c in name_c or ok_c in id_c:
                        val_to_use = str(ov)
                        explicit_override_applied = True
                        break

                profile_value = None

                if current_step_is_date_related and is_datepicker_widget_step:
                    datepicker_context = _last_calendar_type or _pending_direct_date_context
                    if _pending_direct_date_context and not _last_calendar_type:
                        print(
                            f"Skipping orphaned datepicker widget step for {datepicker_context}: {step}"
                        )
                        if step["type"] == "select":
                            previous_step_was_date_related = current_step_is_date_related
                            continue
                    profile_date_value = _get_profile_datepicker_value(
                        step, execution_profile, datepicker_context
                    )
                    if profile_date_value is not None:
                        val_to_use = str(profile_date_value)
                else:
                    profile_value = _get_profile_value_for_field(
                        profile_field,
                        execution_profile,
                        step_type=step["type"],
                        tag_name=tag_name,
                    )
                    if profile_value is not None:
                        val_to_use = profile_value
                        if current_step_is_date_related:
                            _pending_direct_date_context = date_field_context

                # When the user specifies only certain fields in their instructions,
                # we avoid auto-filling *extra* text inputs, but still allow
                # dropdowns (select elements) to be populated so core choices like
                # marital status, gender, county, city, state, subgroup, etc. are
                # always set.
                if restrict_generated_fields and not explicit_override_applied and step["type"] == "input":
                    if _is_generated_data_field(
                        label, name, id_attr, text, selector
                    ) and profile_field not in requested_execution_fields:
                        print(
                            f"Skipping unrequested input field: {label or name or id_attr or selector}"
                        )
                        previous_step_was_date_related = current_step_is_date_related
                        continue

                if not str(val_to_use).strip():
                    print(
                        f"Skipping empty recorded input/select step with no override: {step}"
                    )
                    previous_step_was_date_related = current_step_is_date_related
                    continue

                if step["type"] == "select" and current_step_is_date_related:
                    success = _handle_datepicker_select(driver, step, val_to_use)
                    if success:
                        print(
                            f"Successfully acted on '{label or name or id_attr or selector}' with '{val_to_use}'"
                        )
                    else:
                        print(
                            f"Warning: could not find or act on datepicker select step: {step}"
                        )
                    if current_step_is_date_related:
                        time.sleep(1.5)
                    else:
                        time.sleep(1.0)
                    previous_step_was_date_related = current_step_is_date_related
                    continue

                success = False
                strategies = _build_input_locators(step)
                select_candidates = []

                if step["type"] == "select":
                    select_candidates = _candidate_values(
                        val_to_use,
                        step.get("value", ""),
                        step.get("text", ""),
                    )

                for _ in range(30):
                    for strategy in strategies:
                        elements = _find_elements_for_strategy(driver, strategy)
                        if not elements:
                            continue
                        for element in elements:
                            try:
                                if step["type"] == "select":
                                    for candidate in select_candidates:
                                        if _set_select_value(element, candidate):
                                            val_to_use = candidate
                                            success = True
                                            break
                                    if success:
                                        break
                                else:
                                    combined = f"{label_l} {name_l} {id_l}" if label and name and id_attr else f"{label.lower()} {name.lower()} {id_attr.lower()}"
                                    prefer_typing = any(
                                        token in combined
                                        for token in [
                                            "ssn",
                                            "phone",
                                            "mobile",
                                            "cell",
                                            "mask",
                                            "date",
                                            "dob",
                                            "zip",
                                            "postal",
                                            "postcode",
                                        ]
                                    )
                                    if _set_text_input(
                                        element, val_to_use, prefer_typing=prefer_typing
                                    ):
                                        success = True
                                        if any(
                                            token in combined for token in ["zip", "postal", "postcode"]
                                        ):
                                            time.sleep(3.0)
                                            try:
                                                _wait_for_page_ready(driver, timeout=5)
                                            except Exception:
                                                pass
                                        break
                            except Exception:
                                continue
                        if success:
                            # Break out of `for strategy in strategies:` loop
                            print(
                                f"Successfully acted on '{label or name or id_attr}' with '{val_to_use}'"
                            )
                            if replay_target_key is not None:
                                completed_replay_targets.add(replay_target_key)
                            combined_check = f"{label.lower()} {name.lower()} {id_attr.lower()}"
                            if profile_field == "effective_date" or "effectivedate" in combined_check:
                                time.sleep(4.0)
                                try:
                                    _wait_for_page_ready(driver, timeout=8)
                                except Exception:
                                    pass
                            if step["type"] == "select" and any(
                                token in combined_check
                                for token in ["county", "city", "state", "subgroup", "billing", "class"]
                            ):
                                time.sleep(3.5)
                                try:
                                    _wait_for_page_ready(driver, timeout=5)
                                except Exception:
                                    pass
                                # Specifically for city/county, wait an extra moment for dependent dropdowns
                                if "county" in combined_check or "state" in combined_check:
                                    time.sleep(4.0)
                            if "ssn" in combined_check or "mask" in combined_check:
                                time.sleep(3.0)
                                try:
                                    _wait_for_page_ready(driver, timeout=5)
                                except Exception:
                                    pass
                            break
                    if success:
                        # Break out of `for _ in range(10):` loop
                        break
                    time.sleep(0.5)
                    
                if not success:
                    print(
                        f"Warning: could not find or act on input/select step: {step}"
                    )

            elif step["type"] in ["click", "click_link"]:
                text = step.get("text", "").strip()
                profile_field = _identify_profile_field(
                    label, name, id_attr, text, selector
                )

                if current_step_is_date_related and _last_calendar_type in {"dob", "hire"}:
                    profile_field = f"{_last_calendar_type}_date"

                if (
                    restrict_generated_fields
                    and _is_generated_data_field(label, name, id_attr, text, selector)
                    and profile_field not in requested_execution_fields
                ):
                    print(
                        f"Skipping unrequested click field: {label or name or id_attr or text or selector}"
                    )
                    previous_step_was_date_related = current_step_is_date_related
                    continue

                if tag_name == "img" and selector and "table" in selector and "ui-datepicker" not in selector:
                    _calendar_open_count += 1
                    _last_calendar_type = "dob" if _calendar_open_count == 1 else "hire"

                # Removed isolated `tag_name == "label"` logic that intercepted robust retries

                if (
                    current_step_is_date_related
                    and selector
                    and "ui-datepicker-div" in selector
                    and text.isdigit()
                ):
                    if _pending_direct_date_context and not _last_calendar_type:
                        print(
                            f"Skipping orphaned datepicker day step for {_pending_direct_date_context}: {step}"
                        )
                        _pending_direct_date_context = None
                        previous_step_was_date_related = current_step_is_date_related
                        continue

                    profile_day_value = _get_profile_datepicker_value(
                        step, execution_profile, _last_calendar_type
                    )
                    if profile_day_value is not None:
                        text = str(profile_day_value)
                    success = _handle_datepicker_day_click(driver, text)
                    if success:
                        print(f"Successfully clicked datepicker day '{text}'")
                    else:
                        print(
                            f"Warning: could not click datepicker day step: {step}"
                        )
                    time.sleep(1.5 if current_step_is_date_related else 1.0)
                    _pending_direct_date_context = None
                    previous_step_was_date_related = current_step_is_date_related
                    continue

                text_l = text.lower()
                id_l = id_attr.lower()
                name_l = name.lower()

                text_c = text_l.replace(" ", "").replace("_", "")
                id_c = id_l.replace(" ", "").replace("_", "")
                name_c = name_l.replace(" ", "").replace("_", "").replace(".", "")

                overridden = False

                if not overridden:
                    for ok, ov in override_data_normalized.items():
                        ok_c = ok.replace(" ", "").replace("_", "").replace(".", "")

                        if (id_c and ok_c in id_c) or (name_c and ok_c in name_c):
                            text = str(ov)
                            overridden = True
                            break

                        if text_c and ok_c in text_c and len(ok_c) > 3:
                            if ok_c in [
                                "county",
                                "state",
                                "city",
                                "zip",
                                "name",
                                "address",
                                "phone",
                            ] and len(text_c) > len(ok_c) + 3:
                                continue
                            text = str(ov)
                            overridden = True
                            break

                if not overridden:
                    profile_value = _get_profile_value_for_field(
                        profile_field,
                        execution_profile,
                        step_type=step["type"],
                        tag_name=tag_name,
                    )
                    if profile_value is not None:
                        text = profile_value
                        overridden = True

                if not any([text, label, aria_label, id_attr, name, selector]):
                    print(
                        f"Skipping empty click step with no identifying attributes: {step}"
                    )
                    continue

                click_blob = _normalize_text(
                    " ".join([id_attr, name, label, text, selector])
                )
                is_decline_all_step = (
                    "btndeclineall" in click_blob
                    or "declineallcoverage" in click_blob
                )
                is_plan_decline_checkbox = (
                    tag_name == "input"
                    and input_type == "checkbox"
                    and "isdeclinedind" in click_blob
                )

                desired_checkbox_state = None
                if tag_name == "input" and input_type == "checkbox":
                    desired_checkbox_state = _infer_checkbox_target_state(step, text)

                force_plan_uncheck = False
                if decline_all_clicked and is_plan_decline_checkbox:
                    plan_decline_checkbox_seen_count += 1
                    if plan_decline_checkbox_seen_count <= 2:
                        desired_checkbox_state = False
                        force_plan_uncheck = True
                    else:
                        desired_checkbox_state = True

                if (
                    decline_all_clicked
                    and is_plan_decline_checkbox
                    and desired_checkbox_state is False
                    and plan_decline_uncheck_count >= 2
                ):
                    print(
                        "Skipping additional plan uncheck after first two selections"
                    )
                    previous_step_was_date_related = current_step_is_date_related
                    continue

                success = False
                chosen_container_id = None
                if "_chzn_o_" in id_attr.lower():
                    chosen_container_id = id_attr.rsplit("_o_", 1)[0]

                if chosen_container_id and text:
                    success = _click_chosen_option(driver, chosen_container_id, text)
                    if success:
                        print(f"Successfully selected chooser option '{text}'")
                        if any(
                            token in chosen_container_id.lower()
                            for token in ["county", "city", "state"]
                        ):
                            time.sleep(1.5)
                            try:
                                _wait_for_page_ready(driver, timeout=5)
                            except Exception:
                                pass

                if success:
                    previous_step_was_date_related = current_step_is_date_related
                    time.sleep(1.5 if current_step_is_date_related else 1.0)
                    continue

                if _click_close_action(driver, step):
                    success = True

                # Input button/submit controls often render label text in @value and may
                # become enabled shortly after section transitions; try a direct ID click first.
                if (
                    id_attr
                    and tag_name == "input"
                    and input_type in {"button", "submit"}
                ):
                    try:
                        button_el = WebDriverWait(driver, 5).until(
                            EC.element_to_be_clickable((By.ID, id_attr))
                        )
                        _prepare_element_for_interaction(button_el)
                        button_el.click()
                        success = True
                    except Exception:
                        pass

                if success:
                    if is_decline_all_step:
                        decline_all_clicked = True
                        plan_decline_uncheck_count = 0
                        plan_decline_checkbox_seen_count = 0
                    if (
                        decline_all_clicked
                        and is_plan_decline_checkbox
                        and desired_checkbox_state is False
                    ):
                        plan_decline_uncheck_count += 1
                        if force_plan_uncheck:
                            print(
                                f"Plan checkbox unticked ({plan_decline_uncheck_count}/2): {id_attr or name or selector}"
                            )
                    print(f"Successfully clicked element '{text or id_attr or name}'")
                    previous_step_was_date_related = current_step_is_date_related
                    time.sleep(1.5 if current_step_is_date_related else 1.0)
                    continue

                strategies = _build_click_locators(step, include_structural_fallback=not overridden)

                if overridden and text:
                    time.sleep(1.0)

                for _ in range(30):
                    for strategy in strategies:
                        elements = _find_elements_for_strategy(driver, strategy)
                        if not elements:
                            continue
                        candidates = []
                        for el in elements:
                            try:
                                if el.is_displayed():
                                    candidates.append(el)
                            except Exception:
                                pass
                        if not candidates:
                            candidates = elements

                        for element in candidates:
                            try:
                                _prepare_element_for_interaction(element)
                                if tag_name == "input" and input_type in {"radio", "checkbox"}:
                                    target_state = True
                                    if input_type == "checkbox" and desired_checkbox_state is not None:
                                        target_state = desired_checkbox_state
                                    if _set_toggle_input(element, desired_checked=target_state):
                                        success = True
                                        break
                                    continue
                                element.click()
                                success = True
                                break
                            except Exception:
                                try:
                                    _prepare_element_for_interaction(element)
                                    if tag_name == "input" and input_type in {"radio", "checkbox"}:
                                        target_state = True
                                        if input_type == "checkbox" and desired_checkbox_state is not None:
                                            target_state = desired_checkbox_state
                                        if _set_toggle_input(element, desired_checked=target_state):
                                            success = True
                                            break
                                        continue
                                    driver.execute_script("arguments[0].click();", element)
                                    success = True
                                    break
                                except Exception:
                                    continue
                        if success:
                            break
                    if success:
                        if is_decline_all_step:
                            decline_all_clicked = True
                            plan_decline_uncheck_count = 0
                            plan_decline_checkbox_seen_count = 0
                        if (
                            decline_all_clicked
                            and is_plan_decline_checkbox
                            and desired_checkbox_state is False
                        ):
                            plan_decline_uncheck_count += 1
                            if force_plan_uncheck:
                                print(
                                    f"Plan checkbox unticked ({plan_decline_uncheck_count}/2): {id_attr or name or selector}"
                                )
                        print(f"Successfully clicked element '{text or id_attr or name}'")
                        if _clean_text(text).lower() in ["submit", "next", "login", "continue"]:
                            time.sleep(3.0)
                            try:
                                _wait_for_page_ready(driver, timeout=10)
                            except Exception:
                                pass
                        break
                    time.sleep(0.5)

                if not success:
                    print(f"Warning: could not find or act on click step: {step}")

            if current_step_is_date_related:
                time.sleep(1.5)
            else:
                time.sleep(1.0)

            previous_step_was_date_related = current_step_is_date_related

        time.sleep(2.0)
        driver.save_screenshot(screenshot_path)
        return True, "Execution complete", screenshot_path, effective_data

    except Exception as e:
        try:
            driver.save_screenshot(screenshot_path)
        except Exception:
            pass
        return False, f"Execution failed: {str(e)}", screenshot_path, effective_data
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    # Test script locally
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "teach":
        start_teaching_mode("http://localhost:5000")
    elif len(sys.argv) > 1 and sys.argv[1] == "run":
        run_execution_mode(
            "http://localhost:5000",
            {"ssn": "999-99-9999", "first": "John", "last": "Doe"},
            headless=False,
        )
