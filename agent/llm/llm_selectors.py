"""LLM-assisted selector generation helpers for the autonomous agent."""

from typing import Optional
from playwright.sync_api import Page
from .llm_instance import get_llm_instance


def extract_table_html_snippet(page: Page, table_identifier: str = "first") -> str:
    """
    Extract a simplified HTML snippet of a table for LLM analysis.
    Strips out script/style tags, minifies class/id names for clarity.
    
    Args:
        page: Playwright page object
        table_identifier: "first" | "all" | CSS selector string
    
    Returns:
        Simplified HTML string (first 2000 chars)
    """
    try:
        if table_identifier == "first":
            snippet = page.evaluate("""(() => {
                var table = document.querySelector('table');
                if (!table) return null;
                return table.outerHTML;
            })()""")
        elif table_identifier == "all":
            snippet = page.evaluate("""(() => {
                var tables = document.querySelectorAll('table');
                if (!tables.length) return null;
                return Array.from(tables).map(t => t.outerHTML).join('\\n---\\n');
            })()""")
        else:
            snippet = page.evaluate("""(selector) => {
                var el = document.querySelector(selector);
                if (!el) return null;
                return el.outerHTML;
            }""", table_identifier)
        
        if not snippet:
            return ""
        
        # Strip script/style tags for clarity
        cleaned = snippet.replace("<script", "<REMOVED_SCRIPT").replace("</script>", "</REMOVED_SCRIPT>")
        cleaned = cleaned.replace("<style", "<REMOVED_STYLE").replace("</style>", "</REMOVED_STYLE>")
        
        return cleaned[:3000]  # Limit to 3000 chars for LLM
    except Exception:
        return ""


def ask_llm_for_selector(
    page: Page,
    html_snippet: str,
    query: str,
    log_fn = None,
) -> Optional[str]:
    """
    Send HTML snippet to LLM and ask it to identify a CSS selector.
    
    Args:
        page: Playwright page (for fallback validation)
        html_snippet: Simplified HTML to analyze
        query: What to find, e.g., "Find the CSS selector for the column containing institution names"
        log_fn: Optional logging callback
    
    Returns:
        CSS selector string (e.g., "table tr td:nth-child(2)") or None
    """
    llm = get_llm_instance(required=True, timeout_seconds=30, retry_interval=1.5, log_fn=log_fn)
    if not llm or not html_snippet:
        return None
    
    prompt = (
        "You are an HTML expert. Given an HTML snippet, you identify CSS selectors that match elements.\n\n"
        f"HTML snippet:\n{html_snippet}\n\n"
        f"Task: {query}\n\n"
        "Return ONLY a single CSS selector string (no explanation, no quotes). "
        "Make it as specific and robust as possible. "
        "Examples: 'table tbody tr:first-child td:nth-child(2)', '.course-name', '[data-id=\"123\"]'\n\n"
        "CSS selector:"
    )
    
    try:
        raw = llm.generate(prompt, max_tokens=150).strip().strip('"').strip("'")
        if log_fn:
            log_fn(f"🤖 LLM selector: {raw}", "AI")
        else:
            print(f"[AI] LLM selector: {raw}")
        return raw
    except Exception as e:
        if log_fn:
            log_fn(f"LLM selector generation failed: {e}", "WARNING")
        return None


def validate_and_use_selector(
    page: Page,
    selector: str,
    expected_count: int = None,
    log_fn = None,
) -> bool:
    """
    Test if a selector works on the current page.
    
    Args:
        page: Playwright page object
        selector: CSS selector to test
        expected_count: If set, check if count matches
        log_fn: Optional logging callback
    
    Returns:
        True if selector is valid and matches expected count (if provided)
    """
    try:
        count = page.locator(selector).count()
        if expected_count is not None:
            matches = count == expected_count
            status = "✓" if matches else "✗"
            msg = f"{status} Selector '{selector}' found {count} element(s) (expected {expected_count})"
        else:
            matches = count > 0
            status = "✓" if matches else "✗"
            msg = f"{status} Selector '{selector}' found {count} element(s)"
        
        if log_fn:
            log_fn(msg, "SYSTEM")
        else:
            print(f"[SYSTEM] {msg}")
        
        return matches
    except Exception as e:
        if log_fn:
            log_fn(f"Selector validation error: {e}", "WARNING")
        return False


def adaptive_selector_finder(
    page: Page,
    query: str,
    table_selector: str = "table",
    expected_count: int = None,
    fallback_selector: str = None,
    log_fn = None,
) -> Optional[str]:
    """
    Full adaptive selector discovery pipeline:
    1. Extract table/section HTML
    2. Ask LLM for matching selector
    3. Validate on page
    4. Fall back to hard-coded selector if LLM fails
    
    Args:
        page: Playwright page object
        query: Natural language description of what to find
        table_selector: CSS selector for the table/section to analyze
        expected_count: Optional validation (e.g., "should find 5 rows")
        fallback_selector: Hard-coded selector if LLM fails
        log_fn: Optional logging callback
    
    Returns:
        Working CSS selector string or None
    """
    def log(msg, level="SYSTEM"):
        if log_fn:
            log_fn(msg, level)
        else:
            print(f"[{level}] {msg}")
    
    # Try LLM first
    log(f"🔍 Finding selector for: {query}", "SYSTEM")
    html = extract_table_html_snippet(page, table_selector)
    if html:
        llm_selector = ask_llm_for_selector(page, html, query, log_fn)
        if llm_selector and validate_and_use_selector(page, llm_selector, expected_count, log_fn):
            return llm_selector
    
    # Fall back to hard-coded selector
    if fallback_selector:
        log(f"📌 Falling back to hard-coded selector: {fallback_selector}", "SYSTEM")
        if validate_and_use_selector(page, fallback_selector, expected_count, log_fn):
            return fallback_selector
    
    log(f"❌ Could not find selector for: {query}", "WARNING")
    return None



