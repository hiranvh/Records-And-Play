"""Form field extraction and deterministic autofill helpers for agent mode."""

import time
from pathlib import Path
from playwright.sync_api import Page, Locator
from core.constants import UTILITY_INPUT_PATTERNS
from core.constants import stop_execution_event
from core.profile import infer_field_value as _infer_field_value
from core.utils import (
    candidate_values as _candidate_values,
    clean_text as _clean_text,
    is_placeholder_select_option as _is_placeholder_select_option,
    normalize_text as _normalize_text,
    safe_lower as _safe_lower,
)

from .interaction import set_select_value as _set_select_value
from .interaction import resolve_select_option as _resolve_select_target_from_options
from .interaction import set_text_input as _set_text_input

def _write_scanned_fields_txt(fields: list, source: str = "scanner") -> None:
    """Persist the latest scanner output in a plain-text debug file."""
    try:
        out_path = Path.cwd() / "scanner_picked_fields.txt"
        lines = [f"source: {source}", f"total_fields: {len(fields or [])}", ""]

        for idx, field in enumerate(fields or [], start=1):
            field_id = str(field.get("id") or "")
            field_name = str(field.get("name") or "")
            field_label = str(field.get("label") or "")
            field_tag = str(field.get("tag") or "")
            field_type = str(field.get("type") or "")
            field_kind = str(field.get("kind") or "")
            field_required = bool(field.get("required", False))
            field_readonly = bool(field.get("read_only", False))
            field_chosen = bool(field.get("chosen", False))
            option_count = len(field.get("options") or [])

            lines.extend([
                f"[{idx}]",
                f"id: {field_id}",
                f"name: {field_name}",
                f"label: {field_label}",
                f"tag: {field_tag}",
                f"type: {field_type}",
                f"kind: {field_kind}",
                f"required: {field_required}",
                f"read_only: {field_readonly}",
                f"chosen: {field_chosen}",
                f"options_count: {option_count}",
                "",
            ])

        with out_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        # Scanner debug output must never break normal execution flow.
        pass


def ensure_form_is_ready(page: Page):
    """
    Pre-flight visibility & expansion (Requirement 1).
    Expands benefit-plan accordions (div_AccodianHead_*), hidden form containers,
    and aria-expanded=false elements before field identification or interaction.
    Must be called after every page navigation or AJAX content load.
    """
    try:
        page.evaluate("""(() => {
            // 1. Benefit-plan accordions: reveal panel and dispatch click to trigger jQuery state
            document.querySelectorAll('[id^="div_AccodianHead_"]').forEach(function(header) {
                var panelId = header.id.replace('div_AccodianHead_', 'div_ShowPlans_');
                var panel = document.getElementById(panelId);
                var isHidden = !panel || panel.style.display === 'none' || panel.hidden
                    || header.classList.contains('ui-accordion-header-collapsed')
                    || header.getAttribute('aria-expanded') === 'false';
                if (isHidden) {
                    header.setAttribute('aria-expanded', 'true');
                    header.classList.remove('ui-accordion-header-collapsed', 'collapsed');
                    if (panel) { panel.style.display = ''; panel.hidden = false; }
                    var link = header.querySelector('a');
                    if (link) link.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                }
            });

            // 2. Expand all other aria-expanded=false nodes (skip navigation/help noise)
            document.querySelectorAll('[aria-expanded="false"]').forEach(function(el) {
                var ctx = (el.textContent + (el.id || '') + (el.className || '')).toLowerCase();
                if (['faq', 'help', 'support', 'nav', 'tooltip', 'cookie'].some(function(p) {
                    return ctx.includes(p);
                })) return;
                el.setAttribute('aria-expanded', 'true');
            });

            // 3. Unhide top-level form containers hidden via inline style
            document.querySelectorAll('form > div, form > fieldset, form > section').forEach(function(el) {
                if (el.style.display === 'none') el.style.display = '';
            });

            // 4. Expand <details> elements
            document.querySelectorAll('details:not([open])').forEach(function(d) { d.open = true; });
        })()""")
        time.sleep(0.3)
    except Exception:
        pass


def ensure_form_sections_visible(page: Page):
    """Expand collapsed sections (legacy wrapper — delegates to ensure_form_is_ready)."""
    ensure_form_is_ready(page)


def scan_fillable_form_fields(page: Page):
    """Scan visible and programmatically-fillable fields.

    Key improvements over a naive querySelectorAll scan:
    - Does NOT skip readOnly fields (date pickers, masked inputs are ReadOnly but fillable via JS/fill).
    - Extracts labels from aria-label, aria-labelledby, placeholder, adjacent <td>/<div> text fallback,
      next siblings as well as previous siblings (FBMC uses both layouts).
    - Includes Chosen.js-hidden <select> elements (display:none but still programmatically settable).
    """
    try:
        fields = page.evaluate(r"""(() => {
            function getLabel(el) {
                // 1. Formal <label for="...">
                if (el.id) {
                    var directLabel = document.querySelector('label[for="' + el.id + '"]');
                    if (directLabel) return directLabel.innerText.trim();
                }
                if (el.labels && el.labels[0]) return el.labels[0].innerText.trim();
                // 2. aria-label attribute
                var ariaLabel = el.getAttribute('aria-label');
                if (ariaLabel) return ariaLabel.trim();
                // 3. aria-labelledby -> referenced element's text
                var labelledBy = el.getAttribute('aria-labelledby');
                if (labelledBy) {
                    var refEl = document.getElementById(labelledBy.split(' ')[0]);
                    if (refEl) return refEl.innerText.trim();
                }
                // 4. Walk DOM ancestors looking for a label sibling or heading
                for (var p = el.parentElement; p && p !== document.body; p = p.parentElement) {
                    // Sibling <label> without a for attribute (wrapping pattern)
                    var sibLabel = p.querySelector('label:not([for])');
                    if (sibLabel && !sibLabel.contains(el)) return sibLabel.innerText.trim();
                    // lb3 FBMC label class (for= attr may mismatch id, so use class-based search)
                    var lb3 = p.querySelector('label.lb3');
                    if (lb3 && !lb3.contains(el)) return lb3.innerText.trim().replace(/[*:\\s]+$/, '').trim();
                    // Previous sibling element text (common MVC / grid pattern)
                    var prev = p.previousElementSibling;
                    if (prev && ['DIV','TD','TH','SPAN','DT','LI','P'].includes(prev.tagName)) {
                        var t = prev.innerText.trim().replace(/[*:]+$/, '');
                        if (t && t.length < 80) return t;
                    }
                    // Next sibling (some layouts put label after the input)
                    var next = p.nextElementSibling;
                    if (next && ['LABEL','SPAN','DIV'].includes(next.tagName)) {
                        var nt = next.innerText.trim().replace(/[*:]+$/, '');
                        if (nt && nt.length < 80) return nt;
                    }
                    // Parent's own first text node / heading (e.g. <div class="form-group"><h5>Label</h5><input>)
                    var heading = p.querySelector('h1,h2,h3,h4,h5,h6,legend');
                    if (heading) {
                        var ht = heading.innerText.trim().replace(/[*:]+$/, '');
                        if (ht && ht.length < 80) return ht;
                    }
                    if (['TD','TH','LI','FIELDSET'].includes(p.tagName || '')) break;
                }
                // 5. placeholder as last resort
                return el.placeholder || el.title || (el.name || el.id || '').replace(/[_\-.\[\]]/g, ' ').trim();
            }

            function isChosenHidden(el) {
                // Chosen.js replaces <select> with a custom div and hides the original.
                // Old versions use _chzn suffix + offscreen positioning; new use _chosen + display:none.
                if (el.tagName !== 'SELECT' || !el.id) return false;
                if (document.getElementById(el.id + '_chosen')) return true;
                if (document.getElementById(el.id + '_chzn')) return true;
                // Detect by adjacent .chosen-container / .chzn-container sibling
                var sib = el.nextElementSibling;
                if (sib && (sib.classList.contains('chosen-container') || sib.classList.contains('chzn-container'))) return true;
                // Detect when select is hidden and parent wraps a chosen container
                var par = el.parentElement;
                if (par && (par.querySelector('.chosen-container, .chzn-container'))) return true;
                // Detect by select's own CSS class (chzn-select, chzn2-select, chosen-select variants)
                var cls = el.className || '';
                if (cls.indexOf('chzn-select') >= 0 || cls.indexOf('chzn2-select') >= 0 || cls.indexOf('chosen-select') >= 0) return true;
                return false;
            }

            return Array.from(document.querySelectorAll('input, textarea, select'))
                .map(function(el, idx) {
                    var style = window.getComputedStyle(el);
                    var tag = el.tagName.toLowerCase();
                    var type = (el.type || '').toLowerCase();

                    // Always include Chosen.js hidden selects — they are programmable
                    var chosenHidden = isChosenHidden(el);

                    var visible = !!(el.offsetWidth || el.offsetHeight)
                        && style.visibility !== 'hidden'
                        && style.display !== 'none';

                    if (!visible && !chosenHidden) return null;
                    if (el.disabled) return null;

                    // Exclude SSN mask display fields (id/name ends with _mask, or has class ssnmask)
                    var elId   = (el.id   || '').toLowerCase();
                    var elName = (el.name || '').toLowerCase();
                    if (elId.endsWith('_mask') || elName.endsWith('_mask') ||
                        el.classList.contains('ssnmask')) return null;

                    // Exclude radio/checkbox inputs that are inside a Yesnoclass btn-group label
                    // — those are handled separately by fill_toggle_groups
                    if (type === 'radio' || type === 'checkbox') {
                        if (el.closest('label.btn') || el.closest('label.Yesnoclass') ||
                            el.closest('label.btn-default')) return null;
                    }

                    if (tag === 'input' && ['hidden', 'submit', 'button', 'file', 'image', 'reset'].includes(type)) return null;

                    var agentIdx = String(idx);
                    el.setAttribute('data-agent-idx', agentIdx);

                    return {
                        agent_idx: agentIdx,
                        tag: tag,
                        id: el.id || '',
                        name: el.name || '',
                        type: type,
                        label: getLabel(el),
                        value: el.value || '',
                        placeholder: el.placeholder || '',
                        checked: el.checked || false,
                        read_only: el.readOnly || false,
                        chosen: chosenHidden,
                        options: (tag === 'select' || chosenHidden) ? Array.from(el.options).map(function(o) {
                            return { text: (o.text || '').trim(), value: o.value };
                        }) : []
                    };
                }).filter(Boolean);
        })()""")
        fields = fields or []
        _write_scanned_fields_txt(fields, source="scan_fillable_form_fields")
        return fields
    except Exception:
        return []


def scan_toggle_groups(page: Page):
    """
    Detect Yes/No radio-button toggle groups (FBMC btn-group pattern).

    FBMC renders these as:
        <label class="lb3" for="Person_..._DisabilityEligibleInd">Disabled</label>
        <div class="btn-group">
            <label class="btn btn-default Yesnoclass"><input type="radio" value="true"/> Yes</label>
            <label class="btn btn-default Yesnoclass active"><input type="radio" value="false"/> No</label>
        </div>

    The question label's `for` attribute uses a SHORT id suffix that does NOT always match the
    input's full id, so we do suffix matching. Tobacco uses a label with NO `for` attribute.

    Returns list of {group_label, options: [{label, input_id, input_name, input_value, checked, label_el_selector}]}
    """
    try:
        return page.evaluate("""(() => {
            function findQuestionLabel(btnGroup, firstInp) {
                var inputId   = firstInp.id   || '';
                var inputName = firstInp.name || '';
                var nameSuffix = inputName.split('.').pop();

                // 1. Exact for= match on the full input id
                if (inputId) {
                    var lbl = document.querySelector('label.lb3[for="' + inputId + '"]');
                    if (lbl) return lbl;
                }
                // 2. Suffix for= match (FBMC uses shorter ids in label[for])
                if (nameSuffix) {
                    var lbl2 = document.querySelector('label.lb3[for$="_' + nameSuffix + '"]');
                    if (lbl2) return lbl2;
                }
                // 3. Walk backwards from btn-group to find previous label.lb3 (Tobacco has no for=)
                for (var p = btnGroup.parentElement; p && p !== document.body; p = p.parentElement) {
                    var prev = p.previousElementSibling;
                    if (prev) {
                        if (prev.tagName === 'LABEL' && prev.classList.contains('lb3')) return prev;
                        var inner = prev.querySelector('label.lb3');
                        if (inner) return inner;
                    }
                    if (['TR', 'TABLE', 'FORM'].includes(p.tagName || '')) break;
                }
                return null;
            }

            var groups = {};

            document.querySelectorAll('div.btn-group').forEach(function(btnGroup) {
                var style = window.getComputedStyle(btnGroup);
                if (style.display === 'none' || style.visibility === 'hidden') return;

                var optionLabels = Array.from(
                    btnGroup.querySelectorAll('label.Yesnoclass, label.btn-default')
                );
                if (!optionLabels.length) return;

                var firstInp = btnGroup.querySelector('input[type="radio"], input[type="checkbox"]');
                if (!firstInp || firstInp.disabled) return;

                var groupKey = firstInp.name || btnGroup.id || '';
                if (!groupKey || groups[groupKey]) return;

                var qLabel = findQuestionLabel(btnGroup, firstInp);
                var questionText = qLabel
                    ? qLabel.innerText.trim().replace(/\\s+/g, ' ').replace(/[*]+/g, '').trim()
                    : '';
                if (!questionText) return;

                var options = [];
                optionLabels.forEach(function(optLbl) {
                    var inp = optLbl.querySelector('input[type="radio"], input[type="checkbox"]');
                    if (!inp) return;
                    var optText = optLbl.innerText.trim();
                    var tagKey = (groupKey + '_' + optText).replace(/[^a-z0-9_]/gi, '_');
                    optLbl.setAttribute('data-agent-toggle', tagKey);
                    options.push({
                        label: optText,
                        input_id: inp.id || '',
                        input_name: inp.name || '',
                        input_value: inp.value || optText,
                        checked: inp.checked,
                        label_el_selector: '[data-agent-toggle="' + tagKey + '"]'
                    });
                });

                if (options.length >= 2) {
                    groups[groupKey] = {group_label: questionText, options: options};
                }
            });

            return Object.values(groups);
        })()""")
    except Exception:
        return []


def scan_conditional_checkboxes(page: Page):
    """Scan checkboxes and radios with enhanced label extraction."""
    try:
        return page.evaluate("""(() => {
            function getLabel(el) {
                if (el.labels && el.labels[0]) return el.labels[0].innerText.trim();
                var a = el.getAttribute('aria-label'); if (a) return a.trim();
                var lb = el.getAttribute('aria-labelledby');
                if (lb) { var r = document.getElementById(lb.split(' ')[0]); if (r) return r.innerText.trim(); }
                for (var p = el.parentElement; p && p !== document.body; p = p.parentElement) {
                    var sl = p.querySelector('label:not([for])');
                    if (sl && !sl.contains(el)) return sl.innerText.trim();
                    var prev = p.previousElementSibling;
                    if (prev && ['DIV','TD','TH','SPAN'].includes(prev.tagName)) {
                        var t = prev.innerText.trim().replace(/[*:]+$/,'');
                        if (t && t.length < 80) return t;
                    }
                    if (['TD','TH','LI'].includes(p.tagName || '')) break;
                }
                return el.placeholder || '';
            }
            return Array.from(document.querySelectorAll('input[type="checkbox"], input[type="radio"]'))
                .map(function(el) {
                    var style = window.getComputedStyle(el);
                    var visible = !!(el.offsetWidth || el.offsetHeight) && style.visibility !== 'hidden';
                    if (!visible || el.disabled) return null;
                    return {
                        id: el.id || '',
                        name: el.name || '',
                        label: getLabel(el),
                        value: el.value || '',
                        type: el.type,
                        checked: el.checked
                    };
                }).filter(Boolean);
        })()""")
    except Exception:
        return []


def should_autoclick_checkbox(checkbox):
    label = _safe_lower(checkbox.get("label", ""))
    if "tobacco" in label or "smoker" in label:
        if _safe_lower(checkbox.get("value")) in ["no", "false", "0"]:
            return True, "Default non-smoker"
    return False, ""


def autoclick_conditional_checkboxes(page: Page):
    checkboxes = scan_conditional_checkboxes(page)
    clicked = False
    for cb in checkboxes:
        should, reason = should_autoclick_checkbox(cb)
        if should and not cb.get("checked"):
            try:
                page.locator(f"#{cb['id']}").first.click(timeout=1000)
                clicked = True
            except: pass
    return clicked


def is_utility_input_field(field) -> bool:
    blob = _normalize_text(field.get("label", "") + field.get("name", "") + field.get("id", ""))
    # Short generic patterns (≤6 chars, e.g. "search", "faq") require an exact blob match to avoid
    # false-positives like "employeesearchsubgroup".  Longer specific patterns still use substring.
    return any(
        (blob == p) if len(p) <= 6 else (p in blob)
        for p in UTILITY_INPUT_PATTERNS
    )


def field_needs_value(field) -> bool:
    val = _clean_text(field.get("value", ""))
    if not val:
        return True

    # For selects/chosen controls, placeholder defaults like "-1", "0", "none",
    # or "Select ..." must be treated as empty so the field gets auto-filled.
    if field.get("tag") == "select" or field.get("chosen"):
        selected_text = ""
        for opt in (field.get("options") or []):
            if _clean_text(opt.get("value", "")) == val:
                selected_text = _clean_text(opt.get("text", ""))
                break
        return _is_placeholder_select_option(selected_text or val, val)

    return val in ["0", "select"]


def _resolve_field_element(page: Page, field) -> Locator:
    agent_idx = field.get("agent_idx")
    if agent_idx:
        try:
            loc = page.locator(f"[data-agent-idx='{agent_idx}']").first
            if loc.count() > 0:
                return loc
        except Exception:
            pass
    if field.get("id"):
        # Attribute selector is safer than #id when IDs contain special chars.
        return page.locator(f"[id='{field['id']}']").first
    if field.get("name"):
        return page.locator(f"[name='{field['name']}']").first
    return None


def fill_toggle_groups(page: Page, execution_profile: dict) -> int:
    """
    Click the correct Yes/No label for all radio toggle groups on the page.
    Uses scan_toggle_groups() to find groups, then identify_profile_field() to
    map the question text to a profile key.
    """
    from core.profile import identify_profile_field as _identify_field, get_profile_value as _get_profile_val

    groups = scan_toggle_groups(page)
    clicked = 0
    for group in groups:
        group_label = group.get("group_label", "")
        options = group.get("options", [])
        if not group_label or not options:
            continue

        # Map group label to a profile field (e.g. "Tobacco use" → "tobacco")
        field_key = _identify_field(label=group_label)
        if not field_key:
            continue

        desired = _get_profile_val(field_key, execution_profile, step_type="select", tag_name="label")
        if not desired:
            continue

        desired_norm = _normalize_text(desired)
        target_option = next(
            (o for o in options if _normalize_text(o.get("label", "")) == desired_norm),
            None
        )
        if not target_option:
            continue

        # Click the label for the desired option if not already selected
        inp_id = target_option.get("input_id", "")
        label_sel = target_option.get("label_el_selector", target_option.get("selector", ""))
        already_checked = target_option.get("checked", False)
        if already_checked:
            continue

        # Click the wrapping <label class="btn Yesnoclass"> element directly.
        # FBMC uses label-wraps-input pattern — clicking the label selects the radio inside it.
        try:
            loc = None
            if label_sel:
                loc = page.locator(label_sel).first
            elif inp_id:
                loc = page.locator(f"label.Yesnoclass:has([id='{inp_id}'])").first
            if loc and loc.count() > 0 and loc.is_visible(timeout=500):
                loc.click(timeout=2000)
                clicked += 1
        except Exception:
            pass

    return clicked


def get_semantic_map(page: Page) -> list:
    """
    Extract: Return a unified semantic map of all visible fields on the current page.
    Aggregates text inputs/selects, Yes/No toggle groups, and conditional checkboxes.
    Each entry contains: label, id, name, tag, type, value, placeholder, options, kind.
    Use this function to see what's on the screen before deciding what to fill.
    """
    semantic = []

    # Text inputs, selects (including Chosen.js)
    for f in (scan_fillable_form_fields(page) or []):
        kind = "chosen" if f.get("chosen") else ("readonly" if f.get("read_only") else "text")
        semantic.append({
            "label": f.get("label", ""),
            "id": f.get("id", ""),
            "name": f.get("name", ""),
            "tag": f.get("tag", ""),
            "type": f.get("type", ""),
            "value": f.get("value", ""),
            "placeholder": f.get("placeholder", ""),
            "options": [],
            "kind": kind,
        })

    # Yes/No toggle radio groups
    for g in (scan_toggle_groups(page) or []):
        opts = g.get("options") or []
        active_val = next((o.get("input_value", "") for o in opts if o.get("checked")), "")
        semantic.append({
            "label": g.get("group_label", ""),
            "id": opts[0].get("input_id", "") if opts else "",
            "name": opts[0].get("input_name", "") if opts else "",
            "tag": "input",
            "type": "radio",
            "value": active_val,
            "placeholder": "",
            "options": [o.get("label", "") for o in opts],
            "kind": "toggle",
        })

    # Conditional checkboxes
    for c in (scan_conditional_checkboxes(page) or []):
        semantic.append({
            "label": c.get("label", ""),
            "id": c.get("id", ""),
            "name": c.get("name", ""),
            "tag": "input",
            "type": "checkbox",
            "value": "true" if c.get("checked") else "false",
            "placeholder": "",
            "options": [],
            "kind": "checkbox",
        })

    return semantic


def fill_remaining_visible_inputs(page: Page, override_data_normalized, execution_profile=None, max_seconds: float = 20.0) -> int:
    """Heuristic autofill pass with a hard time cap.

    ``max_seconds`` prevents AJAX cascade loops (zip → county → city → state)
    from stalling the entire workflow for minutes.
    """
    _start = time.monotonic()

    def _timed_out() -> bool:
        return (time.monotonic() - _start) >= max_seconds

    ensure_form_sections_visible(page)

    # Fill Yes/No toggle groups (Disabled, Medicare Eligible, Tobacco, etc.)
    fill_toggle_groups(page, execution_profile or {})

    filled = 0

    for pass_num in range(1, 3):
        if stop_execution_event.is_set() or _timed_out():
            break

        fields = scan_fillable_form_fields(page)
        fields = [f for f in (fields or []) if not is_utility_input_field(f)]
        
        pass_triggers_ajax = False

        for field in fields:
            if stop_execution_event.is_set() or _timed_out():
                break

            if not field_needs_value(field): 
                continue

            val = _infer_field_value(field.get("label"), field.get("name"), field.get("id"), execution_profile=execution_profile)
            
            _field_combined = _normalize_text(f"{field.get('label','')} {field.get('name','')} {field.get('id','')}")
            
            if not val:
                # Fallback purely for required dropdowns that must have *something*
                if field.get("tag") == "select" or field.get("chosen"):
                    is_required_dropdown = any(k in _field_combined for k in ["county", "city", "state", "billing", "subgroup", "class", "employee"])
                    if is_required_dropdown:
                        valid_opts = [o for o in (field.get("options") or []) if o.get("value") and not _is_placeholder_select_option(o.get("text", ""), o.get("value", ""))]
                        if valid_opts:
                            val = valid_opts[0].get("text", "") or valid_opts[0].get("value", "")

            if not val:
                continue

            is_ajax_trigger = any(k in _field_combined for k in ["zip", "postal", "billing", "subgroup"])

            loc = _resolve_field_element(page, field)
            if not loc:
                continue

            try:
                if loc.count() == 0: 
                    continue
            except Exception:
                continue

            success = False
            try:
                if field.get("tag") == "select" or field.get("chosen"):
                    resolved = _resolve_select_target_from_options(loc, val)
                    candidates = _candidate_values(resolved, val, str(val).upper() if val else "", str(val).title() if val else "")
                    
                    for candidate in candidates:
                        if _set_select_value(loc, candidate):
                            success = True
                            break
                    if not success:
                        fallback = _resolve_select_target_from_options(loc, "")
                        if fallback and _set_select_value(loc, fallback):
                            success = True
                            
                elif field.get("read_only") and field.get("id"):
                    el_id = field.get("id", "")
                    safe_val = str(val).replace("'", "\'")
                    page.evaluate(f"(() => {{ var el = document.getElementById('{el_id}'); if (!el) return; var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value'); if(nativeSetter && nativeSetter.set) nativeSetter.set.call(el, '{safe_val}'); else el.value = '{safe_val}'; el.dispatchEvent(new Event('input', {{bubbles: true}})); el.dispatchEvent(new Event('change', {{bubbles: true}})); }})()")
                    success = True
                else:
                    if _set_text_input(loc, val):
                        success = True
            except Exception:
                pass
                
            if success:
                filled += 1
                time.sleep(0.05)
                if is_ajax_trigger:
                    pass_triggers_ajax = True
                    break

        if not pass_triggers_ajax or _timed_out():
            break
            
        time.sleep(0.5)
        try:
            page.wait_for_load_state("networkidle", timeout=800)
        except Exception:
            pass
        time.sleep(0.5)

    return filled

def html_extract_fields(page: Page) -> list:
    """
    Extract every visible form field from the current page HTML and return a
    structured list ready to be sent to an LLM agent.

    For each field we capture:
      id, name, tag, type, label, placeholder, value, required,
      options (for select/radio/checkbox groups), read_only, chosen, kind.

    This is the 'Extract' step for the LLM-mounted agent workflow.
    """
    try:
        fields = page.evaluate(r"""(() => {
            var SKIP_TYPES = new Set(['submit','button','hidden','image','reset','file']);
            var anonymousCounter = 0;

            function getLabel(el) {
                // 1. Explicit <label for=...>
                if (el.id) {
                    var lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl) return lbl.innerText.replace(/\\s+/g,' ').trim();
                }
                // 2. Wrapping <label>
                if (el.labels && el.labels[0]) return el.labels[0].innerText.replace(/\\s+/g,' ').trim();
                // 3. aria-label / aria-labelledby
                var a = el.getAttribute('aria-label');
                if (a) return a.trim();
                var lb = el.getAttribute('aria-labelledby');
                if (lb) { var r = document.getElementById(lb.split(' ')[0]); if (r) return r.innerText.trim(); }
                // 4. Walk ancestors for sibling/cell label text
                for (var p = el.parentElement; p && p !== document.body; p = p.parentElement) {
                    var sibLabel = p.querySelector('label:not([for])');
                    if (sibLabel && !sibLabel.contains(el)) return sibLabel.innerText.replace(/\s+/g,' ').trim();
                    var sib = p.previousElementSibling;
                    if (sib && ['DIV','TD','TH','SPAN','DT','LI','P'].includes(sib.tagName)) {
                        var t = sib.innerText.replace(/\\s+/g,' ').trim().replace(/:$/, '');
                        if (t && t.length < 80 && t.length > 1) return t;
                    }
                    var next = p.nextElementSibling;
                    if (next && ['LABEL','SPAN','DIV'].includes(next.tagName)) {
                        var nextText = next.innerText.replace(/\s+/g,' ').trim().replace(/:$/, '');
                        if (nextText && nextText.length < 80 && nextText.length > 1) return nextText;
                    }
                    // lb3 FBMC label class anywhere in ancestor
                    var lb3 = p.querySelector('label.lb3');
                    if (lb3) return lb3.innerText.replace(/\\s+/g,' ').trim();
                    var heading = p.querySelector('h1,h2,h3,h4,h5,h6,legend');
                    if (heading) {
                        var ht = heading.innerText.replace(/\s+/g,' ').trim().replace(/:$/, '');
                        if (ht && ht.length < 80) return ht;
                    }
                    if (['TD','TH','LI','TR'].includes((p.tagName || ''))) break;
                }
                return el.placeholder || el.title || (el.name || el.id || '').replace(/[_\-.\[\]]/g, ' ').trim();
            }

            function isVisible(el) {
                var s = window.getComputedStyle(el);
                return !!(el.offsetWidth || el.offsetHeight) &&
                       s.visibility !== 'hidden' && s.display !== 'none';
            }

            function isChosenHidden(el) {
                if (el.tagName !== 'SELECT' || !el.id) return false;
                if (document.getElementById(el.id + '_chosen')) return true;
                if (document.getElementById(el.id + '_chzn')) return true;
                // Detect by adjacent .chosen-container / .chzn-container sibling
                var sib = el.nextElementSibling;
                if (sib && (sib.classList.contains('chosen-container') || sib.classList.contains('chzn-container'))) return true;
                // Detect when select is hidden and parent wraps a chosen container
                var par = el.parentElement;
                if (par && (par.querySelector('.chosen-container, .chzn-container'))) return true;
                return false;
            }

            var results = [];
            var seen = new Set();

            document.querySelectorAll('input, textarea, select').forEach(function(el) {
                var key = el.id || el.name || ('anon__' + (++anonymousCounter));
                if (seen.has(key)) return;
                if (el.disabled) return;
                var chosen = isChosenHidden(el);
                if (!isVisible(el) && !chosen) return;
                var tp = (el.type || '').toLowerCase();
                if (SKIP_TYPES.has(tp)) return;
                // Skip _mask / ssnmask display-only fields
                if ((el.id && el.id.endsWith('_mask')) || (el.className && el.className.includes('ssnmask'))) return;
                // Skip radio/checkbox inside btn-group (handled as toggle groups)
                if ((tp === 'radio' || tp === 'checkbox') && el.closest('label.btn, label.Yesnoclass')) return;

                seen.add(key);
                var tag = el.tagName.toLowerCase();
                var opts = [];
                if (tag === 'select' || chosen) {
                    opts = Array.from(el.options).map(function(o) {
                        return { text: o.text.trim(), value: o.value };
                    }).filter(function(o) { return o.text && o.value !== ''; });
                }
                // For radio groups collect all options
                if (tp === 'radio' && el.name) {
                    document.querySelectorAll('input[type=radio][name="' + el.name + '"]').forEach(function(r) {
                        var rl = getLabel(r) || r.value;
                        if (rl) opts.push({ text: rl, value: r.value });
                    });
                }

                results.push({
                    id: el.id || '',
                    name: el.name || '',
                    tag: tag,
                    type: tp || tag,
                    label: getLabel(el),
                    placeholder: el.placeholder || '',
                    value: el.value || '',
                    required: el.required || el.getAttribute('aria-required') === 'true' || false,
                    read_only: el.readOnly || false,
                    chosen: chosen,
                    options: opts,
                    kind: chosen ? 'chosen' : (el.readOnly ? 'readonly' : (opts.length > 0 ? 'select' : tp || 'text'))
                });
            });

            // Also capture Yes/No toggle groups (div.btn-group)
            document.querySelectorAll('div.btn-group').forEach(function(grp) {
                var inputs = grp.querySelectorAll('input[type=radio], input[type=checkbox]');
                if (!inputs.length) return;
                var first = inputs[0];
                var key = 'toggle__' + (first.name || first.id);
                if (seen.has(key)) return;
                seen.add(key);

                // Find question label: lb3 in preceding siblings or ancestors
                var qLabel = '';
                for (var p = grp.parentElement; p && p !== document.body; p = p.parentElement) {
                    var lb3 = p.previousElementSibling
                        ? p.previousElementSibling.querySelector('label.lb3') || (p.previousElementSibling.matches('label.lb3') ? p.previousElementSibling : null)
                        : null;
                    if (lb3) { qLabel = lb3.innerText.replace(/\\s+/g,' ').trim(); break; }
                }

                var opts = [];
                grp.querySelectorAll('label.Yesnoclass, label.btn').forEach(function(lbl) {
                    var inp = lbl.querySelector('input');
                    if (!inp) return;
                    opts.push({
                        text: lbl.innerText.replace(/\\s+/g,' ').trim(),
                        value: inp.value,
                        checked: inp.checked,
                        input_id: inp.id,
                        label_selector: inp.id ? 'label.Yesnoclass:has(#' + inp.id + ')' : ''
                    });
                });

                results.push({
                    id: first.id || '',
                    name: first.name || '',
                    tag: 'input',
                    type: 'radio',
                    label: qLabel,
                    placeholder: '',
                    value: Array.from(inputs).filter(function(i) { return i.checked; }).map(function(i) { return i.value; })[0] || '',
                    required: false,
                    read_only: false,
                    chosen: false,
                    options: opts,
                    kind: 'toggle'
                });
            });

            return results;
        })()""")
        fields = fields or []
        _write_scanned_fields_txt(fields, source="html_extract_fields")
        return fields
    except Exception:
        return []
