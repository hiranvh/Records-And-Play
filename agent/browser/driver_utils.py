"""Playwright browser lifecycle helpers for the agent runtime."""

from playwright.sync_api import Page, sync_playwright


def create_webdriver(headless: bool = False):
    """Create and return a Playwright browser session plus a single page."""
    pw = sync_playwright().start()
    
    if headless:
        # Standard HD explicitly defined for background processing
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
    else:
        # For recording mode, use the user's natural window size and scale
        browser = pw.chromium.launch(
            headless=False,
            args=["--start-maximized"]
        )
        context = browser.new_context(no_viewport=True)
    
    page = context.new_page()
    return pw, browser, page


def save_full_page_screenshot(
    page: Page,
    path: str,
) -> None:
    """Save a full-page screenshot to ``path``."""
    try:
        page.screenshot(path=path, full_page=True)
    except Exception:
        pass
