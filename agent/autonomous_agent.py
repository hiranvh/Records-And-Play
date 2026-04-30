"""
agent/autonomous_agent.py
--------------------------
OOP Autonomous Form-Filling Agent

Architecture:
  AgentConfig       — Input configuration dataclass
  LoginHandler      — Login page detection and credential filling
  AlertMonitor      — JS dialog and on-page error monitoring
  GroupNavigator    — Group listing navigation and group selection
  ModalHandler      — Custom modal / overlay handling
  PageFiller        — Field scanning, value resolution, form filling, CTA
  AutonomousAgent   — Main orchestrator (coordinates all classes above)

Public API (backward-compatible):
  run_autonomous_agent(start_url, override_data, group_name, headless,
                       max_pages, update_callback) -> dict
"""

from __future__ import annotations
import json
import re
import time
import os
import threading
from dataclasses import dataclass, field as dc_field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import unquote

from playwright.sync_api import Page, Dialog

from core.constants import stop_execution_event
from core.config_loader import load_credentials_for_url
from core.utils import normalize_text

from .browser.driver_utils import create_webdriver, save_full_page_screenshot
from .browser.form_scanner import (
    html_extract_fields,
    ensure_form_is_ready,
    fill_toggle_groups,
)
from .browser.interaction import set_text_input, set_select_value
from .llm.llm_instance import get_llm_instance
from .llm.llm_selectors import adaptive_selector_finder
from .workflow_context import build_step_aware_fill_prompt, extract_enrollment_signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(msg: str, level: str = "SYSTEM", fn: Optional[Callable] = None):
    print(f"[{level}] {msg}")
    if fn:
        try:
            fn(msg, level)
        except Exception:
            fn(msg)


def _archive_page_html(page: Page, page_num: int, label: str = "") -> dict:
    """
    Capture full page HTML and metadata for later analysis.
    Returns metadata dict and saves HTML to disk.
    """
    try:
        url = page.url
        title = page.title()
        html_content = page.content()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create archive directory
        archive_dir = os.path.join(os.getcwd(), "PageArchive")
        os.makedirs(archive_dir, exist_ok=True)
        
        # Save HTML with clear naming
        safe_label = (label or "page").replace(" ", "_")
        html_file = os.path.join(archive_dir, f"page_{page_num:02d}_{safe_label}_{timestamp}.html")
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        metadata = {
            "page_num": page_num,
            "label": label,
            "timestamp": timestamp,
            "url": url,
            "title": title,
            "html_file": html_file,
            "html_size_bytes": len(html_content.encode("utf-8")),
        }
        return metadata
    except Exception as e:
        _log(f"Failed to archive page HTML: {e}", "WARNING")
        return {}


def _save_page_archive_index(archive_list: list):
    """Save a JSON index of all archived pages for quick reference."""
    try:
        archive_dir = os.path.join(os.getcwd(), "PageArchive")
        os.makedirs(archive_dir, exist_ok=True)
        index_file = os.path.join(archive_dir, "index.json")
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(archive_list, f, indent=2)
        _log(f"📑 Page archive index saved: {index_file}", "SYSTEM")
    except Exception as e:
        _log(f"Failed to save archive index: {e}", "WARNING")


def _take_screenshot(page: Page, label: str = "agent") -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(os.getcwd(), "Screenshots", f"{label}_{ts}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        save_full_page_screenshot(page, path)
    except Exception:
        pass
    return path


def _wait_for_page(page: Page, timeout: int = 10000):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass


def _bootstrap_enrollment_routes(page: Page, log_fn=None) -> bool:
    """
    When the post-login landing page is a shell with no actionable controls,
    try deterministic enrollment URLs before giving up.
    """
    current_url = page.url or ""
    base_url = ""

    # Prefer the in-page baseUrl variable when available (e.g. /employerengage/fbmc).
    try:
        base_url = (page.evaluate("() => (typeof baseUrl === 'string' ? baseUrl : '')") or "").strip()
    except Exception:
        base_url = ""

    root = ""
    m = re.match(r"^(https?://[^/]+)", current_url)
    if m:
        root = m.group(1)

    candidates = []
    if root and base_url.startswith("/"):
        candidates.extend([
            f"{root}{base_url}/Index/Enrollment",
            f"{root}{base_url}/Group/SearchEmployee",
        ])

    trimmed = current_url.rstrip("/")
    if trimmed:
        candidates.extend([
            f"{trimmed}/Index/Enrollment",
            f"{trimmed}/Group/SearchEmployee",
        ])

    if root:
        candidates.extend([
            f"{root}/employerengage/fbmc/Index/Enrollment",
            f"{root}/employerengage/fbmc/Group/SearchEmployee",
        ])

    seen = set()
    for target in candidates:
        if not target or target in seen:
            continue
        seen.add(target)
        try:
            if (page.url or "").rstrip("/") == target.rstrip("/"):
                continue
            page.goto(target, timeout=30000, wait_until="domcontentloaded")
            _wait_for_page(page, 10000)

            after_url = page.url or ""
            after_url_lower = after_url.lower()
            if (
                "/index/enrollment" in after_url_lower
                or "/group/searchemployee" in after_url_lower
                or "/employees" in after_url_lower
            ):
                _log(f"  ✓ Bootstrap navigation succeeded: {after_url}", "SUCCESS", log_fn)
                return True

            # Even if URL did not match expected path, keep route if actionable page appeared.
            try:
                has_action = bool(page.evaluate("""() => {
                    const nav = !!document.querySelector('#BtnAdd, a#BtnAdd, a[data-redirecturl*="/Employees"], a[data-redirecturl*="/New/Employees"]');
                    const fields = document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), select, textarea').length;
                    return nav || fields > 0;
                }"""))
            except Exception:
                has_action = False
            if has_action:
                _log(f"  ✓ Bootstrap route yielded actionable page: {after_url}", "SUCCESS", log_fn)
                return True
        except Exception:
            continue

    return False


def _compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _url_contains_group(url: str, group_name: str) -> bool:
    compact_group = _compact_text(group_name)
    if not compact_group:
        return False
    return compact_group in _compact_text(unquote(url or ""))


def _is_group_listing_page(page: Page) -> bool:
    try:
        return bool(page.evaluate("""() => {
            const hasRows = !!document.querySelector('a.divRedirectGrid, table tbody tr.items, table tbody tr');
            const hasGroupsHeading = Array.from(document.querySelectorAll('h1, h2, h3, h4, .page-heading'))
                .some((node) => /\bgroups\b/i.test((node.innerText || node.textContent || '').trim()));
            const bodyText = (document.body && (document.body.innerText || document.body.textContent) || '').trim();
            const hasResultSummary = /filtered\\s+results\\s+showing/i.test(bodyText);
            const hasGroupColumns = /group\\s+name/i.test(bodyText) && /actions/i.test(bodyText);
            const hasSearchBox = !!document.querySelector('#txtSearch, input[name="KeyWordSearch"], #btnSearch');
            return (hasRows && hasGroupsHeading) || (hasRows && hasResultSummary) || (hasRows && hasGroupColumns) || (hasGroupsHeading && hasSearchBox);
        }"""))
    except Exception:
        return False


def _wait_for_group_navigation(page: Page, start_url: str, group_name: str, timeout_seconds: float = 10.0) -> bool:
    deadline = time.time() + max(1.0, timeout_seconds)
    while time.time() < deadline:
        current_url = page.url or ""
        if current_url != start_url and _url_contains_group(current_url, group_name):
            return True

        selected_group = ""
        try:
            selected_group = page.locator("#account_name_setup, #account_name_setup span, [id*='account_name' i]").first.inner_text(timeout=400).strip()
        except Exception:
            pass
        if selected_group and _compact_text(group_name) in _compact_text(selected_group):
            return True

        if current_url != start_url and not _is_group_listing_page(page):
            return True

        time.sleep(0.35)

    return False


def _require_llm(log_fn=None, context: str = "current task", timeout_seconds: float = 45.0):
    llm = get_llm_instance(
        required=True,
        timeout_seconds=timeout_seconds,
        retry_interval=1.5,
        log_fn=log_fn,
    )
    if not llm:
        _log(f"❌ Ollama LLM unavailable while {context}.", "ERROR", log_fn)
    return llm


def _click_group_navigation_target(page: Page, group_name: str, log_fn=None) -> bool:
    """Try to enter a group by clicking a visible link, button, or table row matching its name."""
    if not group_name:
        return False

    patterns = [group_name.strip()]
    start_url = page.url or ""

    deadline = time.time() + 8.0
    while time.time() < deadline:
        if _is_group_listing_page(page):
            try:
                row_clicked = page.evaluate(
                    """(desiredName) => {
                        function norm(value) {
                            return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                        }

                        function visible(node) {
                            if (!node) return false;
                            const style = window.getComputedStyle(node);
                            return style.visibility !== 'hidden' && style.display !== 'none' && !!(node.offsetWidth || node.offsetHeight);
                        }

                        const desired = norm(desiredName);
                        const rows = Array.from(document.querySelectorAll('table tbody tr.items, table tbody tr'));
                        for (const row of rows) {
                            const rowText = (row.innerText || row.textContent || '').trim();
                            const rowNorm = norm(rowText);
                            if (!rowNorm || (!rowNorm.includes(desired) && !desired.includes(rowNorm))) continue;

                            const targets = [
                                row.querySelector('a.divRedirectGrid'),
                                row.querySelector('a[title="Edit"]'),
                                row.querySelector('a.btnEdit'),
                                row.querySelector('a.grid-Micon.warn'),
                                row.querySelector('a[value="Continue"]'),
                            ].filter(Boolean);

                            for (const target of targets) {
                                if (!visible(target)) continue;
                                target.click();
                                return (target.getAttribute('title') || target.innerText || target.textContent || 'row target').trim();
                            }
                        }
                        return '';
                    }""",
                    group_name,
                )
                if row_clicked:
                    _wait_for_page(page, 10000)
                    if _wait_for_group_navigation(page, start_url, group_name, timeout_seconds=8.0):
                        _log(f"  ✓ Clicked group row target: {row_clicked}", "SUCCESS", log_fn)
                        return True
                    _log(f"  ↺ Group row click did not navigate: {row_clicked}", "WARNING", log_fn)
                
            except Exception:
                pass

        for candidate in patterns:
            try:
                link = page.get_by_role("link", name=re.compile(re.escape(candidate), re.IGNORECASE)).first
                if link.count() > 0 and link.is_visible(timeout=1200):
                    link.scroll_into_view_if_needed()
                    link.click(timeout=5000, no_wait_after=True)
                    _wait_for_page(page, 10000)
                    if _wait_for_group_navigation(page, start_url, group_name, timeout_seconds=8.0):
                        _log(f"  ✓ Clicked group link: {candidate}", "SUCCESS", log_fn)
                        return True
                    _log(f"  ↺ Group link click did not navigate: {candidate}", "WARNING", log_fn)
            except Exception:
                pass

            try:
                button = page.get_by_role("button", name=re.compile(re.escape(candidate), re.IGNORECASE)).first
                if button.count() > 0 and button.is_visible(timeout=1200):
                    button.scroll_into_view_if_needed()
                    button.click(timeout=5000, no_wait_after=True)
                    _wait_for_page(page, 10000)
                    if _wait_for_group_navigation(page, start_url, group_name, timeout_seconds=8.0):
                        _log(f"  ✓ Clicked group button: {candidate}", "SUCCESS", log_fn)
                        return True
                    _log(f"  ↺ Group button click did not navigate: {candidate}", "WARNING", log_fn)
            except Exception:
                pass

            try:
                text_match = page.get_by_text(re.compile(re.escape(candidate), re.IGNORECASE)).first
                if text_match.count() > 0 and text_match.is_visible(timeout=1200):
                    text_match.scroll_into_view_if_needed()
                    text_match.click(timeout=5000, no_wait_after=True)
                    _wait_for_page(page, 10000)
                    if _wait_for_group_navigation(page, start_url, group_name, timeout_seconds=8.0):
                        _log(f"  ✓ Clicked group text target: {candidate}", "SUCCESS", log_fn)
                        return True
                    _log(f"  ↺ Group text click did not navigate: {candidate}", "WARNING", log_fn)
            except Exception:
                pass

        try:
            row_clicked = page.evaluate(
                """(desiredName) => {
                    function norm(value) {
                        return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                    }

                    const desired = norm(desiredName);
                    const selectors = ['a', 'button', 'td', 'tr', '[role="row"]'];
                    for (const selector of selectors) {
                        const nodes = Array.from(document.querySelectorAll(selector));
                        for (const node of nodes) {
                            const text = (node.innerText || node.textContent || '').trim();
                            if (!text) continue;
                            const visible = !!(node.offsetWidth || node.offsetHeight);
                            if (!visible) continue;
                            const textNorm = norm(text);
                            if (!textNorm) continue;
                            if (textNorm === desired || textNorm.includes(desired) || desired.includes(textNorm)) {
                                node.click();
                                return text;
                            }
                        }
                    }
                    return '';
                }""",
                group_name,
            )
            if row_clicked:
                _wait_for_page(page, 10000)
                if _wait_for_group_navigation(page, start_url, group_name, timeout_seconds=8.0):
                    _log(f"  ✓ Clicked group target by page text: {row_clicked}", "SUCCESS", log_fn)
                    return True
                _log(f"  ↺ Group page-text click did not navigate: {row_clicked}", "WARNING", log_fn)
        except Exception:
            pass

        time.sleep(0.75)

    return False


def _set_group_field(page: Page, group_name: str, log_fn=None) -> bool:
    """
    Dynamically find and fill the group/institution/organization field using adaptive selectors.
    Handles dropdowns, searchable fields, and text inputs.
    
    Args:
        page: Playwright page object
        group_name: The group/organization name to set (e.g., "EnrollTech University", "dual")
        log_fn: Optional logging callback
    
    Returns:
        True if group was successfully set
    """
    if not group_name:
        return False
    
    _log(f"🏢 Setting group field to: {group_name}", "SYSTEM", log_fn)

    # First try navigation-style targets used on post-login dashboards, where
    # the employer/group is a visible link or row rather than a form control.
    if _click_group_navigation_target(page, group_name, log_fn=log_fn):
        return True
    
    # Use adaptive selector to find the group field
    selector = adaptive_selector_finder(
        page,
        query=f"Find the field to select or enter the group/organization '{group_name}'",
        table_selector=None,
        fallback_selector="[name*='group' i], [id*='organization' i], [name*='institution' i], select[name*='org' i]",
        log_fn=log_fn,
    )
    
    if not selector:
        _log(f"  ✗ Could not find group field", "WARNING", log_fn)
        return False
    
    try:
        locator = page.locator(selector).first
        if locator.count() == 0:
            _log(f"  ✗ Group field selector '{selector}' matched no elements", "WARNING", log_fn)
            return False
        
        if not locator.is_visible(timeout=2000):
            _log(f"  ✗ Group field is not visible", "WARNING", log_fn)
            return False
        
        # Check if it's a select dropdown
        tag = locator.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            # Try to find matching option (exact or fuzzy match)
            options_text = locator.evaluate(
                "el => Array.from(el.options || []).map(o => o.text.trim())"
            )
            # Exact match first
            match = next((opt for opt in options_text if opt.lower() == group_name.lower()), None)
            # Partial match if no exact match
            if not match:
                match = next((opt for opt in options_text if group_name.lower() in opt.lower()), None)
            
            if match:
                locator.select_option(label=match)
                _log(f"  ✓ Selected group: {match}", "SUCCESS", log_fn)
                time.sleep(0.5)
                _wait_for_page(page, 3000)
                return True
            else:
                _log(f"  ✗ Group '{group_name}' not found in dropdown options: {options_text[:5]}", "WARNING", log_fn)
                return False
        
        # Otherwise treat as text input or searchable field
        locator.click()
        time.sleep(0.3)
        locator.fill(group_name)
        
        # Try pressing Enter or Tab to trigger autocomplete/submit
        locator.press("Tab")
        time.sleep(0.5)
        _wait_for_page(page, 3000)
        
        _log(f"  ✓ Entered group: {group_name}", "SUCCESS", log_fn)
        return True
        
    except Exception as e:
        _log(f"  ✗ Error setting group field: {e}", "WARNING", log_fn)
        return False


# ---------------------------------------------------------------------------
# Login handler
# ---------------------------------------------------------------------------

# CSS selectors that typically correspond to username / password fields
_USERNAME_SELECTORS = [
    "input[name='Username']",
]

_PASSWORD_SELECTORS = [
    "input[type='Password']",
]

_LOGIN_BUTTON_SELECTORS = [
    "button:has-text('Login')",
]


def _is_login_page(page: Page) -> bool:
    """Return True if the current page appears to have a login/password form."""
    try:
        # URL-based check first (fast, no DOM query)
        url_lower = (page.url or "").lower()
        if "/account/login" in url_lower or "/login" in url_lower and "returnurl" in url_lower:
            return True
    except Exception:
        pass
    try:
        password_loc = page.locator("input[type='password']").first
        if password_loc.count() == 0:
            return False
        try:
            return password_loc.is_visible(timeout=1000)
        except Exception:
            return True
    except Exception:
        return False


def _wait_for_login_result(page: Page, start_url: str, timeout_seconds: float = 20.0) -> bool:
    """Wait for either a successful login transition or a visible login failure."""
    deadline = time.time() + max(1.0, timeout_seconds)
    while time.time() < deadline:
        try:
            current_url = page.url
        except Exception:
            current_url = start_url

        try:
            if not _is_login_page(page):
                return True
        except Exception:
            pass

        try:
            login_errors = page.evaluate("""(() => {
                const selectors = [
                    '.validation-summary-errors',
                    '.field-validation-error',
                    '.text-danger',
                    '.alert-danger',
                    '.error',
                    '[role="alert"]',
                    '.login-error',
                ];
                const messages = [];
                selectors.forEach((selector) => {
                    document.querySelectorAll(selector).forEach((el) => {
                        const text = (el.innerText || '').trim();
                        const style = window.getComputedStyle(el);
                        const visible = !!(el.offsetWidth || el.offsetHeight)
                            && style.visibility !== 'hidden'
                            && style.display !== 'none';
                        if (visible && text) {
                            messages.push(text);
                        }
                    });
                });
                return messages.slice(0, 10);
            })()""") or []
            if login_errors:
                return False
        except Exception:
            pass

        if current_url and start_url and current_url != start_url:
            try:
                if not _is_login_page(page):
                    return True
            except Exception:
                return True

        time.sleep(0.5)

    try:
        return not _is_login_page(page)
    except Exception:
        return False


def _try_fill_field_by_selectors(
    page: Page,
    selectors: list,
    value: str,
    success_template: str,
    log_fn: Optional[Callable] = None,
):
    """Try selectors in order and fill the first visible match."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0 or not loc.is_visible(timeout=1500):
                continue
            if set_text_input(loc, value, prefer_typing=True, press_tab=True):
                _log(success_template.format(selector=sel), "SUCCESS", log_fn)
                return loc
        except Exception:
            continue
    return None


def _click_actionable_locator(locator, timeout_ms: int = 1200) -> bool:
    """Click a locator if visible/enabled, with JS click fallback."""
    try:
        if locator.count() == 0 or not locator.is_visible(timeout=timeout_ms):
            return False
        try:
            if not locator.is_enabled(timeout=timeout_ms):
                return False
        except Exception:
            pass
        locator.scroll_into_view_if_needed()
        try:
            locator.click(timeout=4000, no_wait_after=True)
        except Exception:
            locator.evaluate("el => el.click()")
        return True
    except Exception:
        return False


def _click_login_submit(page: Page, log_fn: Optional[Callable] = None) -> bool:
    """Click login submit using configured selectors only."""
    for sel in _LOGIN_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel).first
            if _click_actionable_locator(loc, timeout_ms=1200):
                _log(f"  ✓ Login button clicked via '{sel}'", "SUCCESS", log_fn)
                return True
        except Exception:
            continue

    return False


def _do_login(page: Page, creds: dict, log_fn: Optional[Callable] = None) -> bool:
    """
    Fill username + password from creds dict and click the login button.
    Returns True if login was attempted.
    """
    username = creds.get("username", "")
    password = creds.get("password", creds.get("pass", ""))

    if not username or not password:
        _log("No credentials found for this URL in config.properties", "WARNING", log_fn)
        return False

    _log(f"🔑 Login page detected — filling credentials for user: {username}", "SYSTEM", log_fn)

    # Fill username
    username_loc = _try_fill_field_by_selectors(
        page,
        _USERNAME_SELECTORS,
        username,
        "  ✓ Username filled via '{selector}'",
        log_fn=log_fn,
    )

    if username_loc is None:
        _log("  ✗ Could not find username field", "WARNING", log_fn)
        return False

    # Fill password
    password_loc = _try_fill_field_by_selectors(
        page,
        _PASSWORD_SELECTORS,
        password,
        "  ✓ Password filled",
        log_fn=log_fn,
    )

    if password_loc is None:
        _log("  ✗ Could not find password field", "WARNING", log_fn)
        return False

    # Small pause before clicking Login (let any JS validators settle)
    time.sleep(0.75)

    # Force a blur/change cycle on the password input; some login pages only
    # enable the submit action after focus leaves the field.
    if password_loc is not None:
        try:
            password_loc.press("Tab")
        except Exception:
            pass

    start_url = page.url

    # Click Login button
    login_clicked = _click_login_submit(page, log_fn=log_fn)

    if login_clicked:
        _log("⏳ Waiting for post-login page to load...", "SYSTEM", log_fn)
        time.sleep(2)
        _wait_for_page(page, timeout=15000)
        login_ok = _wait_for_login_result(page, start_url, timeout_seconds=20)
        _log(f"🌐 Post-login URL: {page.url}", "SYSTEM", log_fn)
        if not login_ok:
            _log("  ✗ Login submission did not leave the login page", "WARNING", log_fn)
        return login_ok

    _log("  ✗ Could not find a clickable login submit control", "WARNING", log_fn)
    return False


# ---------------------------------------------------------------------------
# Alert / Dialog Handler
# ---------------------------------------------------------------------------

class AlertMonitor:
    def __init__(self):
        self.last_dialog: Optional[str] = None
        self.last_dialog_type: str = ""

    def attach(self, page: Page):
        def _on_dialog(dialog: Dialog):
            self.last_dialog = dialog.message
            self.last_dialog_type = dialog.type
            try:
                dialog.accept()
            except Exception:
                pass
        page.on("dialog", _on_dialog)

    def pop_dialog(self) -> Optional[str]:
        msg = self.last_dialog
        self.last_dialog = None
        return msg

    def scan_page_errors(self, page: Page) -> list:
        try:
            return page.evaluate("""(() => {
                var msgs = [];
                var sel = [
                    '.error', '.alert-danger', '.validation-error',
                    '.field-validation-error', '[class*="error"]',
                    '[class*="alert"]', '.help-block', '.text-danger',
                    '.invalid-feedback', '[role="alert"]',
                ];
                sel.forEach(function(s) {
                    document.querySelectorAll(s).forEach(function(el) {
                        var txt = (el.innerText || '').trim();
                        if (txt && txt.length > 3 && txt.length < 300) msgs.push(txt);
                    });
                });
                return [...new Set(msgs)];
            })()""")
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Human-in-the-loop: popup asking user what to do when agent is stuck
# ---------------------------------------------------------------------------

# Sentinel values returned by the stuck-popup
_USER_ACTION_STOP   = "__STOP__"
_USER_ACTION_SKIP   = "__SKIP__"
_USER_ACTION_RETRY  = "__RETRY__"


def _ask_user_what_to_do(page: Page, reason: str, log_fn=None) -> str:
    """
    Show a tkinter popup with a screenshot of the current page state,
    a description of why the agent is stuck, and a text box for the user
    to type an instruction.

    Returns one of:
      _USER_ACTION_STOP   — stop the agent entirely
      _USER_ACTION_SKIP   — skip this page / move on
      _USER_ACTION_RETRY  — retry CTA/navigation discovery
      "<user text>"       — a natural-language instruction to execute
    """
    shot_path = _take_screenshot(page, "stuck")
    _log(f"🛑 Agent stuck: {reason}", "WARNING", log_fn)
    _log("📢 Showing user prompt popup...", "SYSTEM", log_fn)

    # Tkinter popups are not safe from the web-app worker thread on Windows.
    # Fall back cleanly instead of crashing the whole agent process.
    if threading.current_thread() is not threading.main_thread():
        _log(
            f"⚠️ Popup disabled in worker thread. Screenshot saved at: {shot_path}. Falling back to skip.",
            "WARNING",
            log_fn,
        )
        return _USER_ACTION_SKIP

    import tkinter as tk
    from tkinter import scrolledtext

    result: dict = {"action": _USER_ACTION_STOP}

    # Build thumbnail from screenshot if Pillow is available
    photo_image = None
    try:
        from PIL import Image, ImageTk  # type: ignore[reportMissingImports]
        img = Image.open(shot_path)
        img.thumbnail((480, 270))
        # tkinter needs a reference kept alive
        _ask_user_what_to_do._thumb = ImageTk.PhotoImage(img)
        photo_image = _ask_user_what_to_do._thumb
    except Exception:
        pass

    def _submit(event=None):
        txt = text_box.get("1.0", tk.END).strip()
        result["action"] = txt if txt else _USER_ACTION_SKIP
        root.destroy()

    def _skip():
        result["action"] = _USER_ACTION_SKIP
        root.destroy()

    def _retry():
        result["action"] = _USER_ACTION_RETRY
        root.destroy()

    def _stop():
        result["action"] = _USER_ACTION_STOP
        root.destroy()

    root = tk.Tk()
    root.title("⚠️  Agent Needs Help")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.configure(bg="#1e1e2e")

    # ── Header ───────────────────────────────────────────────────────────────
    header = tk.Label(
        root,
        text="🤖  Agent is stuck — what should I do?",
        font=("Segoe UI", 13, "bold"),
        fg="#cdd6f4", bg="#1e1e2e",
        pady=10,
    )
    header.pack(fill=tk.X, padx=16)

    # ── Reason ───────────────────────────────────────────────────────────────
    reason_lbl = tk.Label(
        root,
        text=f"Reason:  {reason}",
        font=("Segoe UI", 10),
        fg="#f38ba8", bg="#1e1e2e",
        wraplength=460, justify="left",
    )
    reason_lbl.pack(fill=tk.X, padx=16)

    # ── URL ──────────────────────────────────────────────────────────────────
    try:
        current_url = page.url
    except Exception:
        current_url = "unknown"
    url_lbl = tk.Label(
        root,
        text=f"URL:  {current_url[:80]}",
        font=("Segoe UI", 9),
        fg="#a6adc8", bg="#1e1e2e",
        wraplength=460, justify="left",
    )
    url_lbl.pack(fill=tk.X, padx=16, pady=(2, 8))

    # ── Screenshot thumbnail ──────────────────────────────────────────────────
    if photo_image:
        img_lbl = tk.Label(root, image=photo_image, bg="#1e1e2e", bd=2, relief="groove")
        img_lbl.pack(padx=16, pady=(0, 8))
    else:
        tk.Label(
            root,
            text=f"[Screenshot saved: {shot_path}]",
            font=("Segoe UI", 9, "italic"),
            fg="#a6adc8", bg="#1e1e2e",
        ).pack(padx=16, pady=(0, 8))

    # ── Instruction input ─────────────────────────────────────────────────────
    tk.Label(
        root,
        text="Type an instruction (or leave blank to skip):",
        font=("Segoe UI", 10),
        fg="#cdd6f4", bg="#1e1e2e",
    ).pack(anchor="w", padx=16)

    text_box = scrolledtext.ScrolledText(
        root,
        height=3, width=56,
        font=("Segoe UI", 10),
        bg="#313244", fg="#cdd6f4",
        insertbackground="white",
        bd=0, relief="flat",
    )
    text_box.pack(padx=16, pady=6)
    text_box.bind("<Control-Return>", _submit)

    # ── Hint ─────────────────────────────────────────────────────────────────
    tk.Label(
        root,
        text="e.g.  'click the Next button'  |  'navigate to /enroll'  |  blank = skip page",
        font=("Segoe UI", 8, "italic"),
        fg="#6c7086", bg="#1e1e2e",
    ).pack(padx=16, pady=(0, 8))

    # ── Buttons ───────────────────────────────────────────────────────────────
    btn_frame = tk.Frame(root, bg="#1e1e2e")
    btn_frame.pack(fill=tk.X, padx=16, pady=(4, 14))

    btn_style = dict(font=("Segoe UI", 10, "bold"), width=12, bd=0, relief="flat", cursor="hand2")

    tk.Button(btn_frame, text="✔ Submit",  bg="#a6e3a1", fg="#1e1e2e", command=_submit, **btn_style).pack(side=tk.LEFT, padx=4)
    tk.Button(btn_frame, text="↩ Retry",   bg="#89b4fa", fg="#1e1e2e", command=_retry,  **btn_style).pack(side=tk.LEFT, padx=4)
    tk.Button(btn_frame, text="⏭ Skip",    bg="#fab387", fg="#1e1e2e", command=_skip,   **btn_style).pack(side=tk.LEFT, padx=4)
    tk.Button(btn_frame, text="⛔ Stop",   bg="#f38ba8", fg="#1e1e2e", command=_stop,   **btn_style).pack(side=tk.RIGHT, padx=4)

    text_box.focus_set()
    root.update_idletasks()
    # Centre on screen
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")
    root.mainloop()

    return result["action"]


def _click_instruction_target(page: Page, target: str, log_fn=None) -> bool:
    """Best-effort click on a visible target using text/link/button heuristics."""
    target = str(target or "").strip()
    if not target:
        return False

    patterns = [target]
    for candidate in patterns:
        try:
            link = page.get_by_role("link", name=re.compile(re.escape(candidate), re.IGNORECASE)).first
            if link.count() > 0 and link.is_visible(timeout=1500):
                link.scroll_into_view_if_needed()
                link.click(timeout=5000, no_wait_after=True)
                _log(f"  ✓ Clicked link '{candidate}'", "SUCCESS", log_fn)
                time.sleep(1.0)
                _wait_for_page(page, 8000)
                return True
        except Exception:
            pass

        try:
            button = page.get_by_role("button", name=re.compile(re.escape(candidate), re.IGNORECASE)).first
            if button.count() > 0 and button.is_visible(timeout=1500):
                button.scroll_into_view_if_needed()
                button.click(timeout=5000, no_wait_after=True)
                _log(f"  ✓ Clicked button '{candidate}'", "SUCCESS", log_fn)
                time.sleep(1.0)
                _wait_for_page(page, 8000)
                return True
        except Exception:
            pass

        try:
            text_loc = page.get_by_text(re.compile(re.escape(candidate), re.IGNORECASE)).first
            if text_loc.count() > 0 and text_loc.is_visible(timeout=1500):
                text_loc.scroll_into_view_if_needed()
                text_loc.click(timeout=5000, no_wait_after=True)
                _log(f"  ✓ Clicked text target '{candidate}'", "SUCCESS", log_fn)
                time.sleep(1.0)
                _wait_for_page(page, 8000)
                return True
        except Exception:
            pass

    return False


def _execute_instruction_heuristic(page: Page, instruction: str, log_fn=None) -> bool:
    """Handle simple manual instructions without requiring the LLM."""
    instr = str(instruction or "").strip()
    if not instr:
        return False

    match = re.search(
        r"\b(?:click|select|choose|open|press|tap)\b\s+(?:the\s+)?(.+)$",
        instr,
        flags=re.IGNORECASE,
    )
    if match:
        target = match.group(1).strip(" .:-")
        target = re.sub(r"^(?:this is .*? flow\s+)?(?:so|sow)\s+", "", target, flags=re.IGNORECASE)
        if _click_instruction_target(page, target, log_fn=log_fn):
            return True

    # Fallback: look for meaningful quoted text or known page action words.
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", instr)
    for target in quoted:
        if _click_instruction_target(page, target, log_fn=log_fn):
            return True

    for keyword in ["employee administration", "employee details", "enrollment", "next", "submit", "continue"]:
        if keyword in instr.lower():
            if _click_instruction_target(page, keyword, log_fn=log_fn):
                return True

    return False


def _execute_user_instruction(page: Page, instruction: str, profile: dict, log_fn=None) -> bool:
    """
    Parse a natural-language instruction from the user and execute it on the page.
    Returns True if an action was taken.
    """
    if not instruction or instruction in (_USER_ACTION_STOP, _USER_ACTION_SKIP, _USER_ACTION_RETRY):
        return False

    _log(f"▶ Executing user instruction: '{instruction}'", "SYSTEM", log_fn)

    # Simple rule-based parsing first —————————————————————————
    instr_lower = instruction.lower().strip()

    # Navigate to a URL
    if instr_lower.startswith(("go to ", "navigate to ", "open ", "visit ")):
        url_part = re.split(r"go to |navigate to |open |visit ", instr_lower, maxsplit=1)[-1].strip()
        if not url_part.startswith("http"):
            url_part = "https://" + url_part
        try:
            page.goto(url_part, timeout=30000, wait_until="domcontentloaded")
            _wait_for_page(page, 10000)
            _log(f"  ✓ Navigated to {url_part}", "SUCCESS", log_fn)
            return True
        except Exception as e:
            _log(f"  ✗ Navigation error: {e}", "WARNING", log_fn)
            return False

    # Use LLM to map instruction → selector + action ─────────────────────────
    llm = _require_llm(log_fn=log_fn, context="interpreting a manual instruction", timeout_seconds=60)
    if not llm:
        return False

    # Collect visible interactive elements for context
    try:
        el_ctx = page.evaluate("""(() => {
            var els = [];
            document.querySelectorAll('button, a, input[type=submit], input[type=button], [role=button]')
                .forEach(function(el) {
                    var txt = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
                    if (txt && el.offsetParent !== null) els.push(txt.slice(0, 60));
                });
            return [...new Set(els)].slice(0, 25);
        })()""")
    except Exception:
        el_ctx = []

    prompt = (
        "You are a browser automation assistant.\n"
        f"The user said: \"{instruction}\"\n\n"
        f"Visible interactive elements on the page: {json.dumps(el_ctx)}\n\n"
        "Return a JSON object with:\n"
        "  action: 'click' | 'fill' | 'navigate'\n"
        "  target: element text or URL\n"
        "  value: (only for fill — the value to type)\n"
        "Reply ONLY with valid JSON."
    )
    try:
        raw = llm.generate(prompt, max_tokens=200)
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e == -1:
            return _execute_instruction_heuristic(page, instruction, log_fn=log_fn)
        cmd = json.loads(raw[s:e+1])
        action = cmd.get("action", "click")
        target = cmd.get("target", "")
        value  = cmd.get("value", "")

        if action == "navigate" and target:
            if not target.startswith("http"):
                target = "https://" + target
            page.goto(target, timeout=30000, wait_until="domcontentloaded")
            _wait_for_page(page, 10000)
            _log(f"  ✓ Navigated to {target}", "SUCCESS", log_fn)
            return True

        if action in ("click", "fill") and target:
            loc = page.get_by_text(re.compile(re.escape(target), re.IGNORECASE)).first
            if loc.count() == 0:
                loc = page.get_by_role("button", name=re.compile(re.escape(target), re.IGNORECASE)).first
            if loc.count() > 0 and loc.is_visible(timeout=2000):
                if action == "fill" and value:
                    loc.fill(value)
                    _log(f"  ✓ Filled '{target}' with '{value}'", "SUCCESS", log_fn)
                else:
                    loc.click(timeout=5000)
                    _log(f"  ✓ Clicked '{target}'", "SUCCESS", log_fn)
                time.sleep(1.5)
                _wait_for_page(page, 8000)
                return True
    except Exception as ex:
        _log(f"  ✗ Instruction execution error: {ex}", "WARNING", log_fn)

    return _execute_instruction_heuristic(page, instruction, log_fn=log_fn)


# ---------------------------------------------------------------------------
# Custom modal / overlay handler
# ---------------------------------------------------------------------------

def _scan_custom_modals(page: Page) -> list:
    """
    Detect visible custom modal dialogs (Bootstrap, jQuery UI, ARIA dialogs,
    div overlays). Ignores hidden or zero-size elements.
    Returns a list of dicts: {text, buttons: [{label, selector}]}
    """
    try:
        return page.evaluate("""(() => {
            var modalSelectors = [
                '.modal.show',
                '.modal[style*="display: block"]',
                '.modal[style*="display:block"]',
                '[role="dialog"]:not([aria-hidden="true"])',
                '[aria-modal="true"]',
                '.ui-dialog',
                '.ui-dialog-content',
                '.popup-content',
                '.overlay-content',
                '.sweet-alert',
                '.swal2-popup',
            ];
            var modals = [];
            for (var i = 0; i < modalSelectors.length; i++) {
                var els = document.querySelectorAll(modalSelectors[i]);
                els.forEach(function(el) {
                    if (el.style.display === 'none' || el.style.visibility === 'hidden') return;
                    var rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) return;
                    var text = (el.innerText || '').trim().slice(0, 500);
                    if (!text) return;
                    var buttons = [];
                    el.querySelectorAll('button, input[type="button"], input[type="submit"], a.btn, [role="button"]').forEach(function(btn) {
                        var label = (btn.innerText || btn.value || btn.getAttribute('aria-label') || '').trim();
                        if (!label) return;
                        var sel = '';
                        if (btn.id) sel = '#' + CSS.escape(btn.id);
                        else if (btn.className) sel = btn.tagName.toLowerCase() + '.' + btn.className.trim().split(/\\s+/)[0];
                        else sel = btn.tagName.toLowerCase();
                        buttons.push({label: label, selector: sel});
                    });
                    modals.push({text: text, buttons: buttons});
                });
            }
            // Deduplicate by text
            var seen = {};
            return modals.filter(function(m) {
                if (seen[m.text]) return false;
                seen[m.text] = true;
                return true;
            });
        })()"""
        )
    except Exception:
        return []


def _handle_modals_with_llm(page: Page, profile: dict, log_fn=None) -> bool:
    """
    Detect custom styled modals (not JS browser dialogs) and use the LLM to
    decide which button to click. Returns True if any modal was handled.
    """
    modals = _scan_custom_modals(page)
    if not modals:
        return False

    llm = _require_llm(log_fn=log_fn, context="handling modal dialogs", timeout_seconds=30)
    handled = False

    for modal in modals:
        text = modal.get("text", "")
        buttons = modal.get("buttons", [])
        if not buttons:
            _log(f"🪟 Modal detected but has no buttons: '{text[:80]}'...", "WARNING", log_fn)
            continue

        _log(f"🪟 Custom modal detected: '{text[:100]}'...", "SYSTEM", log_fn)
        _log(f"   Buttons available: {[b['label'] for b in buttons]}", "SYSTEM", log_fn)

        chosen = None
        if llm:
            prompt = (
                "You are a form-fill assistant. A modal dialog has appeared during an enrollment workflow.\n\n"
                f"Modal text:\n{text}\n\n"
                f"Available buttons: {json.dumps([b['label'] for b in buttons])}\n\n"
                "Which button label should be clicked to proceed with the enrollment workflow? "
                "Reply with ONLY the exact button label text, nothing else."
            )
            try:
                raw = llm.generate(prompt, max_tokens=50).strip().strip('"').strip("'")
                _log(f"  LLM chose modal button: '{raw}'", "AI", log_fn)
                chosen = next(
                    (b for b in buttons if raw.lower() in b["label"].lower() or b["label"].lower() in raw.lower()),
                    None,
                )
            except Exception:
                pass

        # Fallback priority if LLM unavailable or returned no match
        if not chosen:
            for priority_word in ["ok", "yes", "confirm", "proceed", "continue", "close", "accept", "agree"]:
                chosen = next((b for b in buttons if priority_word in b["label"].lower()), None)
                if chosen:
                    break

        if not chosen:
            chosen = buttons[0]  # last resort: first button

        label = chosen.get("label", "")
        sel = chosen.get("selector", "")
        clicked = False

        # Try by role + text first (most reliable)
        try:
            btn_loc = page.get_by_role("button", name=re.compile(re.escape(label), re.IGNORECASE)).first
            if btn_loc.count() > 0 and btn_loc.is_visible(timeout=1500):
                btn_loc.click(timeout=4000)
                clicked = True
        except Exception:
            pass

        # Fallback: CSS selector captured from JS scan
        if not clicked and sel:
            try:
                btn_loc = page.locator(sel).first
                if btn_loc.count() > 0 and btn_loc.is_visible(timeout=1500):
                    btn_loc.click(timeout=4000)
                    clicked = True
            except Exception:
                pass

        if clicked:
            _log(f"  ✓ Modal button '{label}' clicked", "SUCCESS", log_fn)
            time.sleep(1.0)
            _wait_for_page(page, 5000)
            handled = True
        else:
            _log(f"  ✗ Could not click modal button '{label}'", "WARNING", log_fn)

    return handled


# ---------------------------------------------------------------------------
# LLM-powered field-value resolver
# ---------------------------------------------------------------------------

def _llm_resolve_fields(fields: list, profile: dict, page_title: str, log_fn=None, page: Page = None, page_num: int = 0) -> dict:
    llm = _require_llm(log_fn=log_fn, context=f"resolving fields on '{page_title}'", timeout_seconds=45)
    if not llm:
        return {}
    if page is None:
        _log("LLM resolve skipped: missing page context for workflow-aware prompt.", "WARNING", log_fn)
        return {}

    prompt = build_step_aware_fill_prompt(fields, profile, page, page_num)
    try:
        raw = llm.generate(prompt, max_tokens=2000)
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            _log("LLM returned no JSON.", "WARNING", log_fn)
            return {}
        try:
            mapping = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            # Truncated JSON: recover all key-value pairs already closed
            partial = raw[start:end + 1]
            last_comma = partial.rfind(",")
            if last_comma > 0:
                try:
                    mapping = json.loads(partial[:last_comma] + "}")
                except Exception:
                    _log("LLM returned no JSON.", "WARNING", log_fn)
                    return {}
            else:
                _log("LLM returned no JSON.", "WARNING", log_fn)
                return {}
        _log(f"LLM resolved {len(mapping)} field(s) for '{page_title}'", "AI", log_fn)
        return mapping
    except Exception as e:
        _log(f"LLM resolve error: {e}", "WARNING", log_fn)
        return {}


def _profile_resolve_fields(fields: list, profile: dict) -> dict:
    """Rule-based fast path — no LLM call for known profile fields."""
    from core.profile import identify_profile_field, get_profile_value
    mapping: dict = {}
    for f in fields:
        label = f.get("label") or ""
        name  = f.get("name")  or ""
        fid   = f.get("id")    or ""
        key   = identify_profile_field(label=label, name=name, id_attr=fid)
        if key:
            val = get_profile_value(key, profile,
                                    step_type=f.get("type", "input"),
                                    tag_name=f.get("tag", ""))
            if val:
                elem_key = fid or name
                if elem_key:
                    mapping[elem_key] = val
    return mapping


# ---------------------------------------------------------------------------
# Field filler
# ---------------------------------------------------------------------------

def _ids_from_fields(fields: list) -> set:
    """Return the set of id/name keys present in a field list."""
    ids: set = set()
    for f in fields:
        k = f.get("id") or f.get("name")
        if k:
            ids.add(k)
    return ids


def _apply_mapping(page: Page, fields: list, mapping: dict, log_fn=None) -> int:
    field_by_id: dict = {}
    for f in fields:
        key = f.get("id") or f.get("name")
        if key:
            field_by_id[key] = f

    filled = 0
    for fid, value in mapping.items():
        if not value:
            continue
        field = field_by_id.get(fid)
        if not field:
            continue

        kind      = field.get("kind")  or field.get("type") or "text"
        el_id     = field.get("id")    or ""
        el_name   = field.get("name")  or ""
        value_str = str(value)

        try:
            # Build locator
            if el_id:
                loc = page.locator(f"[id='{el_id}']").first
            elif el_name:
                loc = page.locator(f"[name='{el_name}']").first
            else:
                continue

            # ── Toggle (Yes/No radio btn-group) ──────────────────────────
            if kind == "toggle":
                opts = field.get("options") or []
                opt_texts = opts if opts and isinstance(opts[0], dict) else [{"text": str(o)} for o in opts]
                val_norm  = normalize_text(value_str)
                target    = next((o for o in opt_texts if normalize_text(str(o.get("text", ""))) == val_norm), None)
                if target:
                    inp_id    = target.get("input_id") or ""
                    label_sel = target.get("label_selector") or (f"label.Yesnoclass:has([id='{inp_id}'])" if inp_id else "")
                    if label_sel:
                        btn_loc = page.locator(label_sel).first
                        if btn_loc.count() > 0 and not target.get("checked"):
                            btn_loc.click(timeout=3000)
                            filled += 1
                            _log(f"  ✓ toggle '{fid}' → '{value}'", "SUCCESS", log_fn)
                continue

            # ── Chosen.js hidden select ──────────────────────────────────
            if kind == "chosen":
                safe_val = value_str.replace("\\", "\\\\").replace("'", "\\'")
                ok = page.evaluate(f"""(() => {{
                    var el = document.getElementById('{el_id}');
                    if (!el) return false;
                    function norm(v) {{
                        return String(v || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                    }}
                    var desired = norm('{safe_val}');
                    if (!desired) return false;
                    for (var i = 0; i < el.options.length; i++) {{
                        var opt = el.options[i];
                        var tN = norm(opt.text);
                        var vN = norm(opt.value);
                        if (tN === desired || vN === desired
                            || tN.indexOf(desired) >= 0 || desired.indexOf(tN) >= 0) {{
                            if (window.jQuery) {{
                                window.jQuery(el).val(opt.value)
                                    .trigger('chosen:updated')
                                    .trigger('liszt:updated')
                                    .trigger('change')
                                    .trigger('change.select2');
                            }} else {{
                                el.value = opt.value;
                            }}
                            el.dispatchEvent(new Event('input',  {{bubbles: true}}));
                            el.dispatchEvent(new Event('change', {{bubbles: true}}));
                            return true;
                        }}
                    }}
                    return false;
                }})()""")
                if ok:
                    filled += 1
                    _log(f"  ✓ chosen '{fid}' → '{value}'", "SUCCESS", log_fn)
                else:
                    _log(f"  ✗ chosen '{fid}': no matching option for '{value}'", "WARNING", log_fn)
                time.sleep(0.15)
                continue

            # ── Read-only (jQuery datepicker, masked input) ──────────────
            if kind == "readonly":
                safe_val = value_str.replace("'", "\\'")
                page.evaluate(f"""(() => {{
                    var el = document.getElementById('{el_id}');
                    if (!el) return;
                    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, '{safe_val}');
                    el.dispatchEvent(new Event('input',  {{bubbles: true}}));
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                }})()""")
                filled += 1
                _log(f"  ✓ readonly '{fid}' → '{value}'", "SUCCESS", log_fn)
                time.sleep(0.15)
                continue

            # ── Visibility / enabled guard ───────────────────────────────
            try:
                _vis = loc.is_visible(timeout=400)
            except Exception:
                _vis = False
            if not _vis:
                # Might be a Chosen.js-hidden select — try JS fill
                if (kind == "select" or field.get("tag") == "select") and el_id:
                    safe_val = value_str.replace("\\", "\\\\").replace("'", "\\'")
                    ok = page.evaluate(f"""(() => {{
                        var el = document.getElementById('{el_id}');
                        if (!el) return false;
                        function norm(v) {{ return String(v||'').toLowerCase().replace(/[^a-z0-9]/g,''); }}
                        var desired = norm('{safe_val}');
                        for (var i = 0; i < el.options.length; i++) {{
                            var tN = norm(el.options[i].text), vN = norm(el.options[i].value);
                            if (tN === desired || vN === desired || tN.indexOf(desired) >= 0 || desired.indexOf(tN) >= 0) {{
                                if (window.jQuery) {{
                                    window.jQuery(el).val(el.options[i].value)
                                        .trigger('chosen:updated').trigger('liszt:updated')
                                        .trigger('change').trigger('change.select2');
                                }} else {{
                                    el.value = el.options[i].value;
                                }}
                                el.dispatchEvent(new Event('input', {{bubbles:true}}));
                                el.dispatchEvent(new Event('change', {{bubbles:true}}));
                                return true;
                            }}
                        }}
                        return false;
                    }})()""")
                    if ok:
                        filled += 1
                        _log(f"  ✓ select(js) '{fid}' → '{value}'", "SUCCESS", log_fn)
                        time.sleep(0.15)
                    else:
                        _log(f"  ✗ select(js) '{fid}': no matching option for '{value}'", "WARNING", log_fn)
                continue
            try:
                if kind not in ("readonly", "chosen", "toggle") and not loc.is_enabled(timeout=400):
                    continue
            except Exception:
                continue

            # ── Select ───────────────────────────────────────────────────
            if kind == "select" or field.get("tag") == "select":
                # Try Playwright select_option first
                ok = set_select_value(loc, value_str)
                # If Playwright fails, try JS-based fill (handles Chosen.js)
                if not ok and el_id:
                    safe_val = value_str.replace("\\", "\\\\").replace("'", "\\'")
                    ok = page.evaluate(f"""(() => {{
                        var el = document.getElementById('{el_id}');
                        if (!el) return false;
                        function norm(v) {{ return String(v||'').toLowerCase().replace(/[^a-z0-9]/g,''); }}
                        var desired = norm('{safe_val}');
                        for (var i = 0; i < el.options.length; i++) {{
                            var tN = norm(el.options[i].text), vN = norm(el.options[i].value);
                            if (tN === desired || vN === desired || tN.indexOf(desired) >= 0 || desired.indexOf(tN) >= 0) {{
                                if (window.jQuery) {{
                                    window.jQuery(el).val(el.options[i].value)
                                        .trigger('chosen:updated').trigger('liszt:updated')
                                        .trigger('change').trigger('change.select2');
                                }} else {{
                                    el.value = el.options[i].value;
                                }}
                                el.dispatchEvent(new Event('input', {{bubbles:true}}));
                                el.dispatchEvent(new Event('change', {{bubbles:true}}));
                                return true;
                            }}
                        }}
                        return false;
                    }})()""")
                if ok:
                    filled += 1
                    _log(f"  ✓ select '{fid}' → '{value}'", "SUCCESS", log_fn)
                    time.sleep(0.15)
                else:
                    _log(f"  ✗ select '{fid}': failed to set '{value}'", "WARNING", log_fn)
                continue

            # ── Text / textarea / password ───────────────────────────────
            if set_text_input(loc, value_str):
                filled += 1
                _log(f"  ✓ text '{fid}' → '{value}'", "SUCCESS", log_fn)
                time.sleep(0.1)
            else:
                _log(f"  ✗ text '{fid}': failed to set '{value}'", "WARNING", log_fn)

        except Exception as ex:
            _log(f"  ✗ fill error on '{fid}': {ex}", "WARNING", log_fn)

    return filled


# ---------------------------------------------------------------------------
# CTA finder & clicker
# ---------------------------------------------------------------------------

_CTA_TEXTS = [
    "save", "next", "continue", "submit", "proceed",
    "activate and enroll", "enroll", "done", "finish",
    "ok", "confirm", "apply",
]

_CTA_SELECTORS = [
    "input[type='submit']",
    "button[type='submit']",
]


def _click_workflow_shortcut(page: Page, log_fn=None) -> bool:
    """Handle known navigation-only pages in the enrollment workflow."""
    try:
        current_url = (page.url or "").lower()
    except Exception:
        current_url = ""

    shortcut_selectors = []

    if current_url.endswith("/index/enrollment") or "/index/enrollment" in current_url:
        shortcut_selectors.extend([
            ("a[data-redirecturl*='/Employees']", "Employee Administration"),
            ("#dashbourdId a.divRedirect", "Employee Administration"),
        ])

    if current_url.endswith("/employees") or "/group/searchemployee" in current_url or "/employees" in current_url:
        shortcut_selectors.extend([
            ("a#BtnnAdd.AddEmployee", "person_add"),
            ("#BtnnAdd", "person_add"),
            ("a#BtnnAdd", "person_add"),
            ("a.AddEmployee", "person_add"),
            ("[id*='BtnnAdd']", "person_add"),
            ("#BtnAdd", "person_add"),
            ("button#BtnAdd", "person_add"),
            ("a#BtnAdd", "person_add"),
            ("[id*='BtnAdd']", "person_add"),
            ("a[href*='/New/Employee']", "person_add"),
            ("a[href*='/New/Employees']", "person_add"),
            ("a[data-redirecturl*='PersonAdd']", "person_add"),
            ("a[href*='PersonAdd']", "person_add"),
            ("button[title*='Add']", "person_add"),
            ("a[title*='Add']", "person_add"),
            ("button[aria-label*='Add']", "person_add"),
            ("a:has(i.material-icons:has-text('person_add'))", "person_add"),
            ("button:has(i.material-icons:has-text('person_add'))", "person_add"),
            ("a:has(i.fa-user-plus)", "person_add"),
            ("button:has(i.fa-user-plus)", "person_add"),
            ("a[data-redirecturl*='/New/Employees']", "person_add"),
        ])

    for selector, label in shortcut_selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue
            clicked = False
            if _click_actionable_locator(loc, timeout_ms=1200):
                clicked = True
            else:
                try:
                    loc.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    loc.click(timeout=5000, no_wait_after=True, force=True)
                    clicked = True
                except Exception:
                    try:
                        loc.evaluate("el => el.click()")
                        clicked = True
                    except Exception:
                        clicked = False
            if not clicked:
                continue
            _log(f"  ✓ Workflow shortcut clicked: '{label}'", "SUCCESS", log_fn)
            time.sleep(1.5)
            _wait_for_page(page, 10000)
            return True
        except Exception:
            continue

    # Last resort: DOM ranking fallback for icon-only Add Employee controls.
    try:
        clicked = bool(page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('a,button'));
            const visible = (el) => {
                const st = window.getComputedStyle(el);
                return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && st.visibility !== 'hidden' && st.display !== 'none';
            };
            const score = (el) => {
                const blob = [
                    el.id || '',
                    el.getAttribute('name') || '',
                    el.getAttribute('title') || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('data-redirecturl') || '',
                    el.getAttribute('href') || '',
                    el.innerText || '',
                    el.textContent || '',
                    (el.className || '').toString(),
                ].join(' ').toLowerCase();
                let s = 0;
                if (blob.includes('btnadd')) s += 5;
                if (blob.includes('btnnadd')) s += 6;
                if (blob.includes('personadd')) s += 5;
                if (blob.includes('/new/employees')) s += 5;
                if (blob.includes('/new/employee')) s += 5;
                if (blob.includes('addemployee')) s += 5;
                if (blob.includes('person_add')) s += 4;
                if (blob.includes('add employee')) s += 4;
                if (blob.includes('fa-user-plus')) s += 3;
                if (blob.includes('add')) s += 1;
                return s;
            };
            let best = null;
            let bestScore = 0;
            for (const el of items) {
                if (!visible(el)) continue;
                const s = score(el);
                if (s > bestScore) {
                    bestScore = s;
                    best = el;
                }
            }
            if (best && bestScore >= 4) {
                best.click();
                return true;
            }
            return false;
        }"""))
        if clicked:
            _log("  ✓ Workflow shortcut clicked via DOM fallback: 'person_add'", "SUCCESS", log_fn)
            time.sleep(1.5)
            _wait_for_page(page, 10000)
            return True
    except Exception:
        pass

    return False


def _find_and_click_cta(page: Page, exclude_texts: list = None, log_fn=None) -> bool:
    exclude = [normalize_text(t) for t in (exclude_texts or [])]

    if _click_workflow_shortcut(page, log_fn=log_fn):
        return True

    for text in _CTA_TEXTS:
        if normalize_text(text) in exclude:
            continue
        try:
            loc = page.get_by_role("button", name=re.compile(text, re.IGNORECASE)).first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.scroll_into_view_if_needed()
                loc.click(timeout=5000, no_wait_after=True)
                _log(f"  ✓ CTA clicked: '{text}'", "SUCCESS", log_fn)
                time.sleep(1.0)
                _wait_for_page(page, 8000)
                return True
        except Exception:
            pass

        try:
            link = page.get_by_role("link", name=re.compile(text, re.IGNORECASE)).first
            if link.count() > 0 and link.is_visible(timeout=1000):
                link.scroll_into_view_if_needed()
                link.click(timeout=5000, no_wait_after=True)
                _log(f"  ✓ CTA link clicked: '{text}'", "SUCCESS", log_fn)
                time.sleep(1.0)
                _wait_for_page(page, 8000)
                return True
        except Exception:
            pass

        try:
            text_loc = page.get_by_text(re.compile(text, re.IGNORECASE)).first
            if text_loc.count() > 0 and text_loc.is_visible(timeout=1000):
                text_loc.scroll_into_view_if_needed()
                text_loc.click(timeout=5000, no_wait_after=True)
                _log(f"  ✓ CTA text clicked: '{text}'", "SUCCESS", log_fn)
                time.sleep(1.0)
                _wait_for_page(page, 8000)
                return True
        except Exception:
            pass

    for sel in _CTA_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.scroll_into_view_if_needed()
                loc.click(timeout=5000, no_wait_after=True)
                _log("  ✓ CTA clicked (submit input/button)", "SUCCESS", log_fn)
                time.sleep(1.0)
                _wait_for_page(page, 8000)
                return True
        except Exception:
            pass

    return False


# ---------------------------------------------------------------------------
# Success detector
# ---------------------------------------------------------------------------

_SUCCESS_SIGNALS = [
    "success", "complete", "confirmation", "congratulation",
    "thank you", "enrollment complete", "saved", "submitted",
    "finish",
]

# Signals that look like success but are actually login/menu pages — ignore
_FALSE_SUCCESS_KEYWORDS = ["login", "sign in", "log in", "logon", "password"]


def _is_success_page(page: Page) -> bool:
    try:
        body  = page.evaluate("document.body.innerText || ''").lower()[:3000]
        title = page.title().lower()
        url   = page.url.lower()
        combined = title + " " + url + " " + body
        # Must not look like a login page
        if any(k in combined for k in _FALSE_SUCCESS_KEYWORDS) and "input[type='password']" in page.content():
            return False
        return any(s in combined for s in _SUCCESS_SIGNALS)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Error interpreter
# ---------------------------------------------------------------------------

def _interpret_errors_with_llm(errors: list, fields: list, profile: dict, log_fn=None) -> dict:
    if not errors:
        return {}
    llm = _require_llm(log_fn=log_fn, context="interpreting validation errors", timeout_seconds=30)
    if not llm:
        return {}
    field_ids = [f.get("id") or f.get("name") for f in fields if f.get("id") or f.get("name")]
    prompt = (
        "You are a form-fill error fixer.\n"
        "Validation errors on the page:\n"
        f"{json.dumps(errors, indent=2)}\n\n"
        "Available field ids:\n"
        f"{json.dumps(field_ids[:20], indent=2)}\n\n"
        "Employee profile:\n"
        f"{json.dumps(profile, indent=2)}\n\n"
        "Return a JSON object mapping the error field id(s) to their corrected values.\n"
        "Reply ONLY with valid JSON, no commentary."
    )
    try:
        raw = llm.generate(prompt, max_tokens=600)
        s, e = raw.find("{"), raw.rfind("}")
        if s != -1 and e != -1:
            fixes = json.loads(raw[s:e+1])
            _log(f"LLM error-fix: {fixes}", "AI", log_fn)
            return fixes
    except Exception as ex:
        _log(f"LLM error interpret failed: {ex}", "WARNING", log_fn)
    return {}


# ──────────────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentConfig:
    """Immutable configuration for a single autonomous agent run."""
    start_url: str
    override_data: Dict[str, Any] = dc_field(default_factory=dict)
    group_name: Optional[str] = None
    headless: bool = False
    max_pages: int = 25
    update_callback: Optional[Callable] = None


# ──────────────────────────────────────────────────────────────────────────────
# Component classes (delegate to module-level helpers)
# ──────────────────────────────────────────────────────────────────────────────

class LoginHandler:
    """Handles login page detection and credential filling."""

    def is_login_page(self, page: Page) -> bool:
        return _is_login_page(page)

    def do_login(self, page: Page, creds: dict, log_fn: Optional[Callable] = None) -> bool:
        return _do_login(page, creds, log_fn)


class GroupNavigator:
    """Navigates group listing pages and sets the group field."""

    def is_group_listing_page(self, page: Page) -> bool:
        return _is_group_listing_page(page)

    def set_group_field(
        self, page: Page, group_name: str, log_fn: Optional[Callable] = None
    ) -> bool:
        return _set_group_field(page, group_name, log_fn)

    def click_navigation_target(
        self, page: Page, group_name: str, log_fn: Optional[Callable] = None
    ) -> bool:
        return _click_group_navigation_target(page, group_name, log_fn)


class ModalHandler:
    """Handles custom modal dialogs using LLM + rule fallback."""

    def handle_modals(
        self, page: Page, profile: dict, log_fn: Optional[Callable] = None
    ) -> bool:
        return _handle_modals_with_llm(page, profile, log_fn)


class PageFiller:
    """Scans form fields, resolves values, fills them, and clicks the CTA."""

    def resolve_from_profile(self, fields: list, profile: dict) -> dict:
        return _profile_resolve_fields(fields, profile)

    def resolve_with_llm(
        self,
        fields: list,
        profile: dict,
        page_title: str,
        page: Page,
        page_num: int,
        log_fn: Optional[Callable] = None,
    ) -> dict:
        return _llm_resolve_fields(
            fields, profile, page_title, log_fn=log_fn, page=page, page_num=page_num
        )

    def apply_mapping(
        self,
        page: Page,
        fields: list,
        mapping: dict,
        log_fn: Optional[Callable] = None,
    ) -> int:
        return _apply_mapping(page, fields, mapping, log_fn)

    def find_and_click_cta(
        self,
        page: Page,
        exclude_texts: Optional[List[str]] = None,
        log_fn: Optional[Callable] = None,
    ) -> bool:
        return _find_and_click_cta(page, exclude_texts=exclude_texts, log_fn=log_fn)

    def is_success_page(self, page: Page) -> bool:
        return _is_success_page(page)

    def interpret_errors(
        self,
        errors: list,
        fields: list,
        profile: dict,
        log_fn: Optional[Callable] = None,
    ) -> dict:
        return _interpret_errors_with_llm(errors, fields, profile, log_fn)

    @staticmethod
    def ids_from_fields(fields: list) -> set:
        return _ids_from_fields(fields)


# ──────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ──────────────────────────────────────────────────────────────────────────────

class AutonomousAgent:
    """
    Orchestrates the autonomous form-filling workflow:
    LOGIN → GROUP → SCAN → THINK → FILL → ALERT → SUBMIT → LOOP
    """

    def __init__(self, config: AgentConfig) -> None:
        self._cfg             = config
        self._login_handler   = LoginHandler()
        self._alert_monitor   = AlertMonitor()
        self._group_navigator = GroupNavigator()
        self._modal_handler   = ModalHandler()
        self._page_filler     = PageFiller()

    def run(self) -> Dict[str, Any]:
        cfg    = self._cfg
        log_fn = cfg.update_callback

        def log(msg: str, level: str = "SYSTEM") -> None:
            _log(msg, level, log_fn)

        stop_execution_event.clear()

        # ── Credentials & profile ────────────────────────────────────────────
        creds = load_credentials_for_url(cfg.start_url)
        if creds:
            log(f"🔑 Credentials loaded (user: {creds.get('username')})")
        else:
            log("⚠️  No credentials found for this URL", "WARNING")

        group_name = cfg.group_name
        if not group_name:
            for key in ("group_name", "group", "organization", "institution"):
                candidate = str((cfg.override_data or {}).get(key) or "").strip()
                if candidate:
                    group_name = candidate
                    break

        profile = dict(cfg.override_data or {})
        override_norm = {k.lower(): v for k, v in profile.items()}
        if group_name:
            override_norm["billing_location"] = group_name
            profile.setdefault("group_name", group_name)
            profile.setdefault("group", group_name)
            log(f"🏢 Group target: {group_name}")
        if override_norm.get("dob"):
            profile.setdefault("dob_date", override_norm["dob"])
        if override_norm.get("dob_date"):
            profile.setdefault("dob", override_norm["dob_date"])

        log(f"Profile keys: {', '.join(sorted(profile.keys())[:20]) or '(none)'}")

        if not _require_llm(
            log_fn=log_fn,
            context="starting the autonomous enrollment flow",
            timeout_seconds=60,
        ):
            return {
                "status": "failed", "error": "LLM unavailable",
                "pages_processed": 0, "fields_filled": 0,
                "errors": ["LLM unavailable"], "screenshots": [], "page_archive": [],
            }

        # ── Browser setup ─────────────────────────────────────────────────────
        try:
            pw, browser, page = create_webdriver(headless=cfg.headless)
        except Exception as exc:
            log(f"Browser failed to start: {exc}", "ERROR")
            return {"status": "failed", "error": str(exc)}

        self._alert_monitor.attach(page)

        stats: Dict[str, Any] = {
            "pages_processed": 0,
            "fields_filled": 0,
            "errors": [],
            "screenshots": [],
            "page_archive": [],
        }
        logged_in     = False
        group_applied = not bool(group_name)

        try:
            log(f"🌐 Navigating to {cfg.start_url}")
            page.goto(cfg.start_url, timeout=60_000, wait_until="domcontentloaded")
            _wait_for_page(page, 15_000)

            # ── LOGIN ─────────────────────────────────────────────────────────
            if self._login_handler.is_login_page(page):
                log("🔐 Login page detected")
                ok = self._login_handler.do_login(page, creds, log_fn=log_fn)
                if ok:
                    logged_in = True
                    log("✅ Login successful", "SUCCESS")
                else:
                    log("❌ Login failed", "ERROR")
                    _take_screenshot(page, "login_failed")
                    return {"status": "failed", "error": "Login failed", **stats}
            else:
                log("ℹ️  No login page (already authenticated)")

            # ── MAIN LOOP ─────────────────────────────────────────────────────
            for page_num in range(cfg.max_pages):
                if stop_execution_event.is_set():
                    log("⛔ Stop signal received", "WARNING")
                    break

                current_url = page.url
                log(f"\n{'━'*55}")
                log(f"📄 PAGE {page_num + 1}  |  {current_url}")
                log(f"{'━'*55}")

                # Group listing page
                if group_name and self._group_navigator.is_group_listing_page(page):
                    if group_applied:
                        log("↺ Still on Groups page — retrying selection", "WARNING")
                        group_applied = False
                    group_applied = self._group_navigator.set_group_field(
                        page, group_name, log_fn=log_fn
                    )
                    log(
                        f"🏢 Group {'applied' if group_applied else 'not yet selected'}: {group_name}",
                        "SUCCESS" if group_applied else "WARNING",
                    )
                    time.sleep(1.0)
                    continue

                if not group_name and self._group_navigator.is_group_listing_page(page):
                    log("❌ Groups listing but no group_name provided — stopping", "ERROR")
                    stats["errors"].append("Missing group_name on Groups listing page")
                    _take_screenshot(page, "missing_group_name")
                    break

                if group_name and not group_applied:
                    group_applied = self._group_navigator.set_group_field(
                        page, group_name, log_fn=log_fn
                    )
                    if group_applied:
                        log(f"🏢 Group applied: {group_name}", "SUCCESS")
                        current_url = page.url

                # Success check
                if self._page_filler.is_success_page(page):
                    log("🎉 Success page — agent done!", "SUCCESS")
                    _take_screenshot(page, "success")
                    stats["screenshots"].append(_take_screenshot(page, "success_final"))
                    break

                # Session expired re-login
                if self._login_handler.is_login_page(page) and logged_in:
                    log("🔄 Session expired — re-logging in", "WARNING")
                    ok = self._login_handler.do_login(page, creds, log_fn=log_fn)
                    if not ok:
                        log("❌ Re-login failed", "ERROR")
                        break
                    continue

                # ── SCAN ──────────────────────────────────────────────────────
                ensure_form_is_ready(page)
                page_title = ""
                try:
                    page_title = page.title()
                except Exception:
                    pass

                fields = html_extract_fields(page)
                log(f"🔍 Scan: {len(fields)} field(s) on '{page_title}'")

                signals = extract_enrollment_signals(page)
                log(
                    f"📋 Workflow: {signals['flow_type'].replace('_', ' ')} | "
                    f"Step: {signals['step'].replace('_', ' ')} | "
                    f"Confidence: {signals['confidence']:.0%}"
                )

                archive_meta = _archive_page_html(page, page_num + 1, label=page_title or "page")
                if archive_meta:
                    stats["page_archive"].append(archive_meta)

                # Workflow shortcuts (employee list / dashboard)
                page_url_lower = (page.url or "").lower()
                if (
                    "/group/searchemployee" in page_url_lower
                    or "/employees" in page_url_lower
                    or "/index/enrollment" in page_url_lower
                ):
                    if _click_workflow_shortcut(page, log_fn=log_fn):
                        stats["pages_processed"] += 1
                        continue

                if not fields:
                    if not self._handle_no_fields(page, group_name, profile, log_fn=log_fn):
                        break
                    continue

                stats["pages_processed"] += 1

                # ── THINK ─────────────────────────────────────────────────────
                log("🧠 Resolving field values...")
                profile_map = self._page_filler.resolve_from_profile(fields, profile)
                log(f"   Rule-based: {len(profile_map)} field(s)")
                llm_map = self._page_filler.resolve_with_llm(
                    fields, profile, page_title, page, page_num, log_fn=log_fn
                )
                log(f"   LLM-based:  {len(llm_map)} field(s)")
                combined = {**profile_map, **llm_map}
                log(f"   Combined:   {len(combined)} field(s)")

                # ── FILL ──────────────────────────────────────────────────────
                fill_toggle_groups(page, profile)
                n = self._page_filler.apply_mapping(page, fields, combined, log_fn=log_fn)
                stats["fields_filled"] += n
                log(f"✏️  Filled {n} field(s)", "SUCCESS")

                time.sleep(0.5)
                _wait_for_page(page, 5_000)

                # Re-scan for conditionally revealed fields (up to 3 rounds)
                known_ids = PageFiller.ids_from_fields(fields)
                for rescan in range(3):
                    time.sleep(0.4)
                    new_fields = html_extract_fields(page)
                    new_ids    = PageFiller.ids_from_fields(new_fields)
                    revealed   = [
                        f for f in new_fields
                        if (f.get("id") or f.get("name")) not in known_ids
                        and (f.get("id") or f.get("name"))
                    ]
                    if not revealed:
                        break
                    log(f"🔄 Re-scan {rescan + 1}: {len(revealed)} revealed field(s)")
                    r_profile = self._page_filler.resolve_from_profile(revealed, profile)
                    r_llm     = self._page_filler.resolve_with_llm(
                        revealed, profile, page_title, page, page_num, log_fn=log_fn
                    )
                    fill_toggle_groups(page, profile)
                    nr = self._page_filler.apply_mapping(
                        page, revealed, {**r_profile, **r_llm}, log_fn=log_fn
                    )
                    stats["fields_filled"] += nr
                    log(f"   Filled {nr} revealed field(s)", "SUCCESS")
                    known_ids = new_ids
                    _wait_for_page(page, 5_000)

                # ── ALERT ─────────────────────────────────────────────────────
                dialog_msg = self._alert_monitor.pop_dialog()
                if dialog_msg:
                    log(f"⚠️  Alert: {dialog_msg}", "WARNING")
                    stats["errors"].append(dialog_msg)

                page_errors = self._alert_monitor.scan_page_errors(page)
                if page_errors:
                    log(f"⚠️  Page errors: {page_errors}", "WARNING")
                    stats["errors"].extend(page_errors)
                    fixes = self._page_filler.interpret_errors(
                        page_errors, fields, profile, log_fn=log_fn
                    )
                    if fixes:
                        nf = self._page_filler.apply_mapping(page, fields, fixes, log_fn=log_fn)
                        stats["fields_filled"] += nf
                        time.sleep(0.3)

                self._modal_handler.handle_modals(page, profile, log_fn=log_fn)

                # ── SUBMIT ────────────────────────────────────────────────────
                log("🖱️  Clicking CTA...")
                cta_ok = self._page_filler.find_and_click_cta(page, log_fn=log_fn)
                if not cta_ok:
                    if not self._handle_no_cta(page, profile, log_fn=log_fn):
                        break

                time.sleep(2.5)
                _wait_for_page(page, 15_000)

                post_dialog = self._alert_monitor.pop_dialog()
                if post_dialog:
                    log(f"⚠️  Post-submit alert: {post_dialog}", "WARNING")
                    stats["errors"].append(post_dialog)

                self._modal_handler.handle_modals(page, profile, log_fn=log_fn)

            # ── Final ─────────────────────────────────────────────────────────
            try:
                is_ok = self._page_filler.is_success_page(page)
            except Exception:
                is_ok = False

            status = (
                "success" if is_ok
                else ("partial" if stats["pages_processed"] > 0 else "failed")
            )

            try:
                stats["screenshots"].append(_take_screenshot(page, "agent_final"))
            except Exception:
                pass

            _save_page_archive_index(stats["page_archive"])
            log(
                f"\n🏁 Done | status={status} | pages={stats['pages_processed']} | "
                f"filled={stats['fields_filled']} | archived={len(stats['page_archive'])}",
                "SUCCESS" if status == "success" else "SYSTEM",
            )

        finally:
            try:
                if not page.is_closed():
                    browser.close()
                pw.stop()
            except Exception:
                pass

        return {"status": status, **stats}

    # ── Private helpers ───────────────────────────────────────────────────────

    def _handle_no_fields(
        self,
        page: Page,
        group_name: Optional[str],
        profile: dict,
        log_fn: Optional[Callable] = None,
    ) -> bool:
        """Navigate when no form fields found. Returns False to break the main loop."""
        _log("No form fields — looking for navigation", "WARNING", log_fn)

        if _click_workflow_shortcut(page, log_fn=log_fn):
            return True

        effective_group = (
            group_name or profile.get("billing_location") or "Cairn Industries"
        )
        if _click_group_navigation_target(page, str(effective_group), log_fn=log_fn):
            return True

        for css, label in [
            ("#dashbourdId a",                          "dashboard link"),
            ("a.divRedirectGrid",                       "group grid link"),
            ("#BtnAdd, a#BtnAdd",                       "Add Employee"),
            ("a[data-redirecturl*='/Employees']",       "Employee Administration"),
            ("a[data-redirecturl*='/New/Employees']",   "New Employee"),
        ]:
            try:
                loc = page.locator(css).first
                if loc.count() > 0 and loc.is_visible(timeout=1_200):
                    loc.scroll_into_view_if_needed()
                    loc.click(timeout=5_000, no_wait_after=True)
                    _wait_for_page(page, 10_000)
                    _log(f"  → Clicked {label}", "SYSTEM", log_fn)
                    return True
            except Exception:
                continue

        for nav_text in [
            "enroll now", "enroll", "add employee",
            "employee administration", "create", "start",
        ]:
            try:
                link = page.get_by_text(re.compile(nav_text, re.IGNORECASE)).first
                if link.count() > 0 and link.is_visible(timeout=1_000):
                    link.click(timeout=5_000)
                    _log(f"  → Clicked nav: '{nav_text}'", "SYSTEM", log_fn)
                    time.sleep(2)
                    _wait_for_page(page, 10_000)
                    return True
            except Exception:
                pass

        if _bootstrap_enrollment_routes(page, log_fn=log_fn):
            return True

        # Ask user for guidance
        while True:
            action = _ask_user_what_to_do(
                page,
                reason="No form fields and no navigation links found.",
                log_fn=log_fn,
            )
            if action == _USER_ACTION_STOP:
                stop_execution_event.set()
                return False
            if action in (_USER_ACTION_SKIP, _USER_ACTION_RETRY):
                return True
            if _execute_user_instruction(page, action, profile, log_fn=log_fn):
                return True

    def _handle_no_cta(
        self,
        page: Page,
        profile: dict,
        log_fn: Optional[Callable] = None,
    ) -> bool:
        """Ask user when no CTA found. Returns False to break the main loop."""
        while True:
            action = _ask_user_what_to_do(
                page,
                reason="Could not find a Next / Submit / Continue button.",
                log_fn=log_fn,
            )
            if action == _USER_ACTION_STOP:
                stop_execution_event.set()
                return False
            if action == _USER_ACTION_SKIP:
                return True
            if action == _USER_ACTION_RETRY:
                return self._page_filler.find_and_click_cta(page, log_fn=log_fn)
            if _execute_user_instruction(page, action, profile, log_fn=log_fn):
                return True


# ──────────────────────────────────────────────────────────────────────────────
# Backward-compatible public API
# ──────────────────────────────────────────────────────────────────────────────

def run_autonomous_agent(
    start_url: str,
    override_data: Optional[dict] = None,
    group_name: Optional[str] = None,
    headless: bool = False,
    max_pages: int = 25,
    update_callback: Optional[Callable] = None,
) -> dict:
    """
    Entry point for the autonomous form-filling agent.
    Delegates to AutonomousAgent.run() — all existing callers continue to work.
    """
    config = AgentConfig(
        start_url=start_url,
        override_data=override_data or {},
        group_name=group_name,
        headless=headless,
        max_pages=max_pages,
        update_callback=update_callback,
    )
    return AutonomousAgent(config).run()

