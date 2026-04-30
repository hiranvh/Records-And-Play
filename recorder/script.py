"""
recorder.script
---------------
JavaScript injection for browser event capture.

Only records events from elements the user actually interacts with:
  - change  → text inputs, selects, checkboxes, radios
  - click   → buttons, links, Chosen.js dropdowns

No full-page scanning — if the user did not click it, it is not recorded.
"""


class RecorderScript:
    """Holds all JavaScript needed to capture browser interactions."""

    INJECT_JS: str = r"""() => {
        if (window.__recorder_injected) return;
        window.__recorder_injected = true;
        window.__rec_events = [];

        var FEEDBACK_LIMIT = 4;
        var FEEDBACK_STORAGE_KEY = '__recorder_feedback_state_v1';

        function _text(value) {
            return String(value || '').replace(/\s+/g, ' ').trim();
        }

        function _escapeHtml(value) {
            return String(value || '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        }

        function _loadFeedbackState() {
            try {
                var raw = sessionStorage.getItem(FEEDBACK_STORAGE_KEY);
                if (raw) {
                    var parsed = JSON.parse(raw);
                    if (parsed && typeof parsed === 'object') {
                        return {
                            count: Number(parsed.count || 0),
                            recent: Array.isArray(parsed.recent) ? parsed.recent.slice(0, FEEDBACK_LIMIT) : [],
                        };
                    }
                }
            } catch (e) {}
            return { count: 0, recent: [] };
        }

        var __feedbackState = _loadFeedbackState();

        function _saveFeedbackState() {
            try {
                sessionStorage.setItem(FEEDBACK_STORAGE_KEY, JSON.stringify(__feedbackState));
            } catch (e) {}
        }

        function _ensureFeedbackWindow() {
            if (!document.body) return null;

            var panel = document.getElementById('__recorderFeedbackWindow');
            if (panel) return panel;

            panel = document.createElement('div');
            panel.id = '__recorderFeedbackWindow';
            panel.setAttribute('aria-live', 'polite');
            panel.setAttribute('data-recorder-feedback', 'true');
            panel.style.cssText = [
                'position:fixed',
                'right:12px',
                'top:12px',
                'width:280px',
                'max-width:calc(100vw - 24px)',
                'overflow:hidden',
                'z-index:2147483647',
                'background:rgba(15,23,42,0.88)',
                'color:#e2e8f0',
                'border:1px solid rgba(148,163,184,0.22)',
                'border-radius:12px',
                'box-shadow:0 10px 24px rgba(0,0,0,0.22)',
                'font:11px/1.4 Segoe UI,Arial,sans-serif',
                'backdrop-filter:blur(6px)',
                'pointer-events:none',
            ].join(';');

            panel.innerHTML =
                '<div style="display:flex;align-items:flex-start;gap:9px;padding:8px 10px;">' +
                    '<span style="display:inline-block;width:8px;height:8px;margin-top:4px;border-radius:999px;background:#22c55e;box-shadow:0 0 10px rgba(34,197,94,0.55);flex-shrink:0;"></span>' +
                    '<div style="flex:1;min-width:0;">' +
                        '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">' +
                            '<div style="font-weight:700;font-size:11px;letter-spacing:.02em;white-space:nowrap;">Recording Feedback</div>' +
                            '<div id="__recorderFeedbackCount" style="font-size:10px;font-weight:700;color:#86efac;background:rgba(34,197,94,0.12);padding:2px 7px;border-radius:999px;flex-shrink:0;">0</div>' +
                        '</div>' +
                        '<div id="__recorderFeedbackLatest" style="margin-top:4px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Live page capture preview</div>' +
                        '<div id="__recorderFeedbackMeta" style="margin-top:2px;font-size:10px;color:#94a3b8;">Waiting for captured fields...</div>' +
                    '</div>' +
                '</div>' +
                '';

            document.body.appendChild(panel);
            return panel;
        }

        function _renderFeedbackWindow() {
            if (!document.body) {
                if (!window.__recorderFeedbackWaiting) {
                    window.__recorderFeedbackWaiting = true;
                    document.addEventListener('DOMContentLoaded', function() {
                        window.__recorderFeedbackWaiting = false;
                        _renderFeedbackWindow();
                    }, { once: true });
                }
                return;
            }

            var panel = _ensureFeedbackWindow();
            if (!panel) return;

            var countEl = document.getElementById('__recorderFeedbackCount');
            var latestEl = document.getElementById('__recorderFeedbackLatest');
            var metaEl = document.getElementById('__recorderFeedbackMeta');
            if (!countEl || !latestEl || !metaEl) return;

            countEl.textContent = String(__feedbackState.count || 0);

            if (!__feedbackState.recent.length) {
                latestEl.textContent = 'Live page capture preview';
                metaEl.textContent = 'Interact with the page. Captured fields appear here.';
                return;
            }

            latestEl.textContent = __feedbackState.recent[0].text || 'Captured step';
            metaEl.textContent = 'Last captured at ' + (__feedbackState.recent[0].ts || '--:--:--');
        }

        function _describeFeedback(ev) {
            var action = _text(ev.type || 'step').replace(/_/g, ' ');
            var target = _text(ev.label || ev.text || ev.name || ev.id || ev.selector || ev.tag || 'field');
            var sensitiveBlob = _text([
                ev.label,
                ev.name,
                ev.id,
                ev.placeholder,
                ev.input_type,
            ].join(' ')).toLowerCase();
            var rawValue = _text(ev.value || (ev.type === 'select' ? ev.text : ''));
            var preview = '';

            if (rawValue) {
                if (_text(ev.input_type).toLowerCase() === 'password' || /password|passwd|pwd|ssn|social security|tax id|tin/.test(sensitiveBlob)) {
                    preview = '[masked]';
                } else {
                    preview = rawValue.length > 42 ? rawValue.slice(0, 39) + '...' : rawValue;
                }
            }

            if (preview) return action + ': ' + target + ' -> ' + preview;
            return action + ': ' + target;
        }

        function _noteFeedback(ev) {
            __feedbackState.count = Number(__feedbackState.count || 0) + 1;
            __feedbackState.recent.unshift({
                text: _describeFeedback(ev),
                ts: new Date().toLocaleTimeString('en-US', { hour12: false }),
            });
            __feedbackState.recent = __feedbackState.recent.slice(0, FEEDBACK_LIMIT);
            _saveFeedbackState();
            _renderFeedbackWindow();
        }

        _renderFeedbackWindow();

        function recordEvent(ev) {
            _noteFeedback(ev);
            // Prefer the exposed Python function (real-time, survives navigation).
            // Fall back to the polling array if not yet available.
            if (typeof window.__recorderPush === 'function') {
                try { window.__recorderPush(ev); return; } catch(e) {}
            }
            window.__rec_events.push(ev);
        }

        function _escape(id) {
            try { return CSS.escape(id); } catch(e) { return id.replace(/([!"#$%&'()*+,.\/:;<=>?@[\\\]^`{|}~])/g, '\\$1'); }
        }

        function _getSelector(el) {
            if (el.id) return '#' + _escape(el.id);
            if (el.name) return '[name="' + el.name + '"]';
            if (el.className && typeof el.className === 'string') {
                var cls = el.className.trim().split(/\s+/).filter(function(c) { return c; }).slice(0, 2).join('.');
                if (cls) return el.tagName.toLowerCase() + '.' + cls;
            }
            return el.tagName.toLowerCase();
        }

        function _getLabel(el) {
            if (el.labels && el.labels[0]) return el.labels[0].innerText.trim();
            var aria = el.getAttribute('aria-label');
            if (aria) return aria.trim();
            var labelledBy = el.getAttribute('aria-labelledby');
            if (labelledBy) {
                var lbl = document.getElementById(labelledBy);
                if (lbl) return (lbl.innerText || '').trim();
            }
            if (el.placeholder) return el.placeholder.trim();
            var prev = el.previousElementSibling;
            if (prev && (prev.tagName === 'LABEL' || prev.tagName === 'SPAN')) {
                return (prev.innerText || '').trim();
            }
            return '';
        }

        function _slug(value) {
            return _text(value)
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, '_')
                .replace(/^_+|_+$/g, '')
                .substring(0, 80);
        }

        function _getHeadings() {
            var seen = {};
            var headings = [];
            var nodes = document.querySelectorAll('h1, h2, h3, legend');
            for (var i = 0; i < nodes.length; i++) {
                var txt = _text(nodes[i].innerText || nodes[i].textContent || '');
                if (!txt || seen[txt]) continue;
                seen[txt] = true;
                headings.push(txt);
                if (headings.length >= 6) break;
            }
            return headings;
        }

        function _getSection(el) {
            var current = el;
            while (current && current !== document.body) {
                var heading = current.querySelector('h1, h2, h3, legend');
                if (heading) {
                    var text = _text(heading.innerText || heading.textContent || '');
                    if (text) return text;
                }
                current = current.parentElement;
            }

            var headings = _getHeadings();
            return headings.length ? headings[0] : '';
        }

        function _getNearbyText(el) {
            var out = [];
            var seen = {};

            function push(value) {
                var text = _text(value);
                if (!text || seen[text]) return;
                seen[text] = true;
                out.push(text);
            }

            push(_getLabel(el));
            push(el.getAttribute('aria-label'));
            if (el.previousElementSibling) push(el.previousElementSibling.innerText || el.previousElementSibling.textContent);
            if (el.nextElementSibling) push(el.nextElementSibling.innerText || el.nextElementSibling.textContent);
            if (el.parentElement) {
                push(el.parentElement.getAttribute('data-section-title'));
                if (el.parentElement.previousElementSibling) {
                    push(el.parentElement.previousElementSibling.innerText || el.parentElement.previousElementSibling.textContent);
                }
            }

            return out.slice(0, 4);
        }

        function _pageMeta() {
            var url = '';
            var path = '';
            try {
                url = String(window.location.href || '');
                path = String(window.location.pathname || '');
            } catch (e) {}

            var headings = _getHeadings();
            var title = _text(document.title || headings[0] || '');
            var pageSeed = [path, headings[0] || title || 'page'].join(' ');
            return {
                page_id: _slug(pageSeed) || 'page',
                page_url: url,
                page_title: title,
                page_path: path,
            };
        }

        function _withPageMeta(ev) {
            var meta = _pageMeta();
            ev.page_id = meta.page_id;
            ev.page_url = meta.page_url;
            ev.page_title = meta.page_title;
            return ev;
        }

        var __lastFocusedDateInput = null;

        function _isDateInput(el) {
            if (!el || el.tagName !== 'INPUT') return false;
            var inputType = _text(el.type || 'text').toLowerCase();
            if (inputType !== 'text') return false;
            var placeholder = _text(el.placeholder || '').toUpperCase();
            if (placeholder === 'MM/DD/YYYY' || placeholder === 'MM/DD/YY') return true;
            if (el.classList && el.classList.contains('hasDatepicker')) return true;
            var hints = _text([el.id, el.name, _getLabel(el), el.className].join(' ')).toLowerCase();
            return hints.indexOf('date') >= 0;
        }

        function _emitDateInputCapture(el) {
            if (!el || !_isDateInput(el)) return;
            var dateValue = _text(el.value || '');
            if (!dateValue) return;
            recordEvent(_withPageMeta({
                type: 'input',
                tag: 'input',
                id: el.id || '',
                name: el.name || '',
                value: dateValue,
                input_type: _text(el.type || 'text').toLowerCase() || 'text',
                selector: _getSelector(el),
                label: _getLabel(el),
                placeholder: el.placeholder || '',
            }));
        }

        function _isChosenHidden(el) {
            if (!el || el.tagName !== 'SELECT' || !el.id) return false;
            if (document.getElementById(el.id + '_chosen')) return true;
            if (document.getElementById(el.id + '_chzn')) return true;
            var sib = el.nextElementSibling;
            if (sib && (sib.classList.contains('chosen-container') || sib.classList.contains('chzn-container'))) return true;
            return false;
        }

        function _isFieldVisible(el) {
            var style = window.getComputedStyle(el);
            return !!(el.offsetWidth || el.offsetHeight)
                && style.visibility !== 'hidden'
                && style.display !== 'none';
        }

        window.__recorderCollectCheckpoint = function() {
            var meta = _pageMeta();
            var headings = _getHeadings();
            var fields = Array.from(document.querySelectorAll('input, textarea, select')).map(function(el) {
                var tag = (el.tagName || '').toLowerCase();
                var inputType = _text(el.type || (tag === 'select' ? 'select-one' : ''));
                var chosen = _isChosenHidden(el);
                var visible = _isFieldVisible(el) || chosen;

                if (!visible || el.disabled) return null;
                if (tag === 'input' && ['hidden', 'submit', 'button', 'file', 'image', 'reset'].indexOf(inputType) >= 0) return null;

                var options = [];
                if (tag === 'select' && el.options) {
                    options = Array.from(el.options).slice(0, 50).map(function(opt) {
                        return {
                            text: _text(opt.text || ''),
                            value: _text(opt.value || ''),
                        };
                    });
                }

                return {
                    tag: tag,
                    input_type: inputType,
                    id: el.id || '',
                    name: el.name || '',
                    label: _getLabel(el),
                    placeholder: el.placeholder || '',
                    selector: _getSelector(el),
                    aria_label: el.getAttribute('aria-label') || '',
                    section: _getSection(el),
                    nearby_text: _getNearbyText(el),
                    required: !!(el.required || el.getAttribute('aria-required') === 'true'),
                    readonly: !!el.readOnly,
                    visible: !!visible,
                    chosen: !!chosen,
                    options: options,
                };
            }).filter(Boolean);

            var signature = [
                meta.page_id,
                meta.page_url,
                meta.page_title,
                fields.map(function(field) {
                    return [field.tag, field.input_type, field.id, field.name, field.label].join(':');
                }).join('|'),
            ].join('|');

            return {
                page_id: meta.page_id,
                url: meta.page_url,
                path: meta.page_path,
                title: meta.page_title,
                headings: headings,
                field_count: fields.length,
                fields: fields,
                signature: signature,
            };
        };

        document.addEventListener('focusin', function(e) {
            var el = e.target;
            if (_isDateInput(el)) {
                __lastFocusedDateInput = el;
            }
        }, true);

        // ── Capture input/select changes (fires on blur + value change) ──────
        document.addEventListener('change', function(e) {
            var el = e.target;
            if (!el) return;

            if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                var t = (el.type || 'text').toLowerCase();
                if (t === 'hidden' || t === 'submit' || t === 'button' || t === 'image' || t === 'reset') return;
                recordEvent(_withPageMeta({
                    type: (t === 'checkbox' || t === 'radio') ? 'toggle' : 'input',
                    tag: el.tagName.toLowerCase(),
                    id: el.id || '',
                    name: el.name || '',
                    value: (t === 'checkbox')
                        ? (el.checked ? 'true' : 'false')
                        : (t === 'radio' ? (el.value || '') : (el.value || '')),
                    text: (t === 'radio') ? _getLabel(el) : '',
                    input_type: t,
                    selector: _getSelector(el),
                    label: _getLabel(el),
                    placeholder: el.placeholder || '',
                }));
            } else if (el.tagName === 'SELECT') {
                var selText = el.options && el.selectedIndex >= 0 && el.options[el.selectedIndex]
                    ? (el.options[el.selectedIndex].text || '').trim()
                    : '';
                recordEvent(_withPageMeta({
                    type: 'select',
                    tag: 'select',
                    id: el.id || '',
                    name: el.name || '',
                    value: el.value || '',
                    text: selText,
                    selector: _getSelector(el),
                    label: _getLabel(el),
                    input_type: 'select-one',
                    placeholder: '',
                }));
            }
        }, true);

        // ── Capture Chosen.js + button/link clicks ───────────────────────────
        document.addEventListener('click', function(e) {
            var datepickerDay = e.target && e.target.closest
                ? e.target.closest('#ui-datepicker-div td a, #ui-datepicker-div a.ui-state-default')
                : null;
            if (datepickerDay) {
                setTimeout(function() {
                    try {
                        var dateEl = null;
                        if (__lastFocusedDateInput && document.contains(__lastFocusedDateInput)) {
                            dateEl = __lastFocusedDateInput;
                        } else {
                            var active = document.activeElement;
                            if (_isDateInput(active)) dateEl = active;
                        }
                        _emitDateInputCapture(dateEl);
                    } catch (err) { /* swallow to keep recorder stable */ }
                }, 0);
            }

            // Chosen.js option click
            var chosenItem = e.target && e.target.closest
                ? e.target.closest('.chosen-results li, .chzn-results li')
                : null;
            if (chosenItem) {
                setTimeout(function() {
                    try {
                        var container = chosenItem.closest('.chosen-container, .chzn-container');
                        var containerId = container && container.id ? container.id : '';
                        var baseId = containerId.replace(/_(chzn|chosen)$/i, '');
                        var selectEl = baseId ? document.getElementById(baseId) : null;
                        var selectedValue = selectEl ? (selectEl.value || '') : '';
                        recordEvent(_withPageMeta({
                            type: 'select',
                            tag: 'select',
                            id: baseId || '',
                            name: selectEl ? (selectEl.name || '') : '',
                            label: selectEl ? _getLabel(selectEl) : '',
                            value: selectedValue || (chosenItem.innerText || '').trim(),
                            text: (chosenItem.innerText || '').trim().substring(0, 100),
                            selector: baseId ? ('#' + _escape(baseId)) : _getSelector(chosenItem),
                            input_type: 'select-one',
                            placeholder: '',
                        }));
                    } catch (err) { /* swallow to keep recorder stable */ }
                }, 0);
                return;
            }

            // Walk up the DOM to find a meaningful clickable ancestor
            var el = e.target;
            while (el && el !== document.body) {
                var tag = el.tagName;
                if (tag === 'A' || tag === 'BUTTON' ||
                    el.getAttribute('role') === 'button' ||
                    el.onclick || el.getAttribute('onclick')) {
                    var itype = (el.type || '').toLowerCase();
                    if (itype === 'hidden') break;
                    recordEvent(_withPageMeta({
                        type: tag === 'A' ? 'click_link' : 'click',
                        tag: tag.toLowerCase(),
                        id: el.id || '',
                        name: el.getAttribute('name') || '',
                        text: (el.innerText || el.value || '').trim().substring(0, 100),
                        selector: _getSelector(el),
                        input_type: itype,
                        label: el.getAttribute('aria-label') || '',
                        value: '',
                        placeholder: '',
                    }));
                    break;
                }
                el = el.parentElement;
            }
        }, true);

        // ── Chosen.js MutationObserver ──────────────────────────────────────
        // Watches .chzn-single / .chosen-single display spans for text changes.
        // When Chosen.js updates the displayed value, fires a select event with
        // the underlying SELECT element's current value.  This is more reliable
        // than depending on the (sometimes absent) native 'change' event that
        // older Chosen.js versions may not dispatch.
        (function() {
            if (typeof MutationObserver === 'undefined') return;
            var _chznSeen = {};
            var _obs = new MutationObserver(function(mutations) {
                for (var mi = 0; mi < mutations.length; mi++) {
                    var m = mutations[mi];
                    var el = (m.type === 'characterData')
                        ? m.target.parentElement
                        : m.target;
                    if (!el) continue;
                    // Walk up to find .chzn-single or .chosen-single
                    var singleEl = null, node = el;
                    while (node && node !== document.body) {
                        var cn = typeof node.className === 'string' ? node.className : '';
                        if (/\bchzn-single\b|\bchosen-single\b/.test(cn)) { singleEl = node; break; }
                        node = node.parentElement;
                    }
                    if (!singleEl) continue;
                    var container = singleEl.parentElement;
                    if (!container || !container.id) continue;
                    var baseId = container.id.replace(/_(chzn|chosen)$/i, '');
                    if (!baseId) continue;
                    var selectEl = document.getElementById(baseId);
                    if (!selectEl || selectEl.tagName !== 'SELECT') continue;
                    var val = selectEl.value || '';
                    if (!val || _chznSeen[baseId] === val) continue;
                    _chznSeen[baseId] = val;
                    var selText = (selectEl.options && selectEl.selectedIndex >= 0
                        && selectEl.options[selectEl.selectedIndex])
                        ? (selectEl.options[selectEl.selectedIndex].text || '').trim() : '';
                    recordEvent(_withPageMeta({
                        type: 'select', tag: 'select',
                        id: baseId, name: selectEl.name || '',
                        label: _getLabel(selectEl),
                        value: val, text: selText,
                        selector: '#' + _escape(baseId),
                        input_type: 'select-one', placeholder: '',
                    }));
                }
            });
            function _startChosenObs() {
                if (document.body) {
                    _obs.observe(document.body, {
                        subtree: true, characterData: true, childList: true,
                    });
                }
            }
            if (document.body) { _startChosenObs(); }
            else { document.addEventListener('DOMContentLoaded', _startChosenObs); }
        })();
    }"""

    DRAIN_JS: str = """() => {
        var evts = window.__rec_events || [];
        window.__rec_events = [];
        return evts;
    }"""

    CHECKPOINT_JS: str = """() => {
        if (typeof window.__recorderCollectCheckpoint === 'function') {
            return window.__recorderCollectCheckpoint();
        }
        return null;
    }"""
