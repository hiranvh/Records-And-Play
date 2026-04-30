"""Playwright interaction helpers used by the autonomous agent."""

from datetime import datetime
from typing import Optional

from playwright.sync_api import Locator

from core.utils import normalize_text

def _convert_date_to_iso(value: str) -> Optional[str]:
    """Convert common UI date formats to ``YYYY-MM-DD`` when possible."""
    raw = str(value).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def prepare_element(locator: Locator) -> None:
    """Scroll element into view and focus it before interaction."""
    try:
        locator.scroll_into_view_if_needed()
        locator.focus()
    except Exception:
        pass


def dispatch_events(locator: Locator) -> None:
    """Fire input, change, and blur DOM events on a locator."""
    try:
        locator.evaluate("""el => {
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur',   { bubbles: true }));
        }""", timeout=2000)
    except Exception:
        pass


def _refresh_select_ui(locator: Locator, target: Optional[str] = None) -> None:
    """Update Chosen.js / Select2 / Bootstrap Select display."""
    try:
        locator.evaluate("""(el, rawTarget) => {
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur',   { bubbles: true }));
            if (window.jQuery) {
                var $s = window.jQuery(el);
                $s.trigger('change').trigger('change.select2').trigger('chosen:updated').trigger('liszt:updated');
            }
            setTimeout(() => {
                el.dispatchEvent(new Event('change', { bubbles: true }));
                if (window.jQuery) window.jQuery(el).trigger('chosen:updated');
            }, 100);
        }""", target)
    except Exception:
        pass


def resolve_select_option(locator: Locator, desired_value: str) -> Optional[str]:
    """Fuzzy-match desired_value against option values/texts."""
    from core.utils import normalize_text as _norm_text
    desired_norm = _norm_text(desired_value)
    try:
        result = locator.evaluate("""(el, desiredNorm) => {
            function norm(v) {
                var s = String(v||'').toLowerCase().replace(/[^a-z0-9]/g, '');
                if (s === 'md') return 'maryland';
                if (s === 'usa' || s === 'us') return 'unitedstates';
                return s;
            }
            desiredNorm = norm(desiredNorm);
            function isPlaceholder(text, value) {
                var tn = norm(text); var vn = norm(value);
                return !tn || tn.includes('select') || tn.includes('choose') || tn.includes('please')
                    || ['', '0', '-1', 'none', 'null'].indexOf(vn) >= 0;
            }
            var opts = Array.from(el.options || []);
            var fallback = null;
            for (var i = 0; i < opts.length; i++) {
                var val = (opts[i].value || '').trim(), txt = (opts[i].text || '').trim();
                if (!fallback && !isPlaceholder(txt, val)) fallback = val || txt;
                if (!desiredNorm) continue;
                var valN = norm(val), txtN = norm(txt);
                if (valN === desiredNorm || txtN === desiredNorm) return val || txt;
            }
            if (desiredNorm === 'male' || desiredNorm === 'female') {
                var initial = desiredNorm[0];
                for (var i = 0; i < opts.length; i++) {
                    var val = (opts[i].value || '').trim(), txt = (opts[i].text || '').trim();
                    if (norm(txt) === desiredNorm || norm(val) === initial) return val || txt;
                }
            }
            for (var i = 0; i < opts.length; i++) {
                var val = (opts[i].value || '').trim(), txt = (opts[i].text || '').trim();
                var valN = norm(val), txtN = norm(txt);
                if (desiredNorm && (txtN.indexOf(desiredNorm) >= 0 || valN.indexOf(desiredNorm) >= 0 || desiredNorm.indexOf(txtN) >= 0)) return val || txt;
            }
            return fallback || null;
        }""", desired_norm)
        return result
    except Exception:
        return None


def set_text_input(
    locator: Locator,
    value: str,
    prefer_typing: bool = False,
    press_tab: bool = False,
) -> bool:
    """Fill a text field (V1-compatible signature)."""
    from core.utils import clean_text
    target = str(value)
    try:
        el_type = locator.get_attribute("type", timeout=2000)
        if el_type == "date":
            iso = _convert_date_to_iso(target)
            if iso:
                locator.fill(iso, timeout=2000)
                return True
        if el_type == "password":
            locator.fill(target, timeout=2000)
            dispatch_events(locator)
            if press_tab:
                locator.press("Tab", timeout=2000)
            return True
    except Exception:
        pass
    try:
        prepare_element(locator)
        if prefer_typing:
            locator.fill("", timeout=2000)
            locator.type(target, delay=50, timeout=2000)
        else:
            locator.fill(target, timeout=2000)
        dispatch_events(locator)
        if press_tab:
            locator.press("Tab", timeout=2000)
        return True
    except Exception:
        return False


def _set_select_value_basic(locator: Locator, value: str) -> bool:
    """Set a <select> element to value (legacy/basic signature)."""
    from core.utils import clean_text
    desired = clean_text(value)
    try:
        prepare_element(locator)
        locator.select_option(value=desired, force=True, timeout=100)
        _refresh_select_ui(locator, desired)
        return True
    except Exception:
        try:
            locator.select_option(label=desired, force=True, timeout=100)
            _refresh_select_ui(locator, desired)
            return True
        except Exception:
            try:
                resolved = resolve_select_option(locator, desired)
                if not resolved:
                    return False
                try:
                    locator.select_option(value=resolved, force=True, timeout=100)
                except Exception:
                    locator.select_option(label=resolved, force=True, timeout=100)
                _refresh_select_ui(locator, resolved)
                return True
            except Exception:
                return False


def set_select_value(*args) -> bool:
    """
    Unified select setter supporting both signatures:
    1) set_select_value(locator, value)
    2) set_select_value(page, locator, value, step)
    """
    if len(args) == 2:
        locator, value = args
        return _set_select_value_basic(locator, value)

    if len(args) != 4:
        return False

    _, locator, value, _ = args

    # Keep the robust select behavior used by the workflow engine.
    try:
        # Try native select first
        locator.select_option(value=value, timeout=100, force=True)
        _refresh_select_widget(locator)
        return True
    except Exception:
        pass
    return False
    try:
        locator.evaluate("""el => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
            
            if (window.jQuery) {
                var $ = window.jQuery;
                $(el).trigger('change')
                     .trigger('change.select2')
                    .trigger('chosen:updated')
                    .trigger('liszt:updated');
            }

            // Re-fire globally in case they listen on document body
            setTimeout(() => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                if (window.jQuery) {
                    window.jQuery(el).trigger('chosen:updated');
                }
            }, 100);
        }""")
    except Exception:
        pass
