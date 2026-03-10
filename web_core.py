import json
import os
import time
from playwright.sync_api import sync_playwright

WORKFLOW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflow.json")


def _save_workflow(url, steps):
    with open(WORKFLOW_FILE, 'w', encoding='utf-8') as f:
        json.dump({"url": url, "steps": steps}, f, indent=4)


def load_workflow():
    if not os.path.exists(WORKFLOW_FILE):
        raise FileNotFoundError("No workflow recorded. Please record a workflow first.")

    with open(WORKFLOW_FILE, 'r', encoding='utf-8') as f:
        payload = json.load(f)

    if isinstance(payload, list):
        return {"url": None, "steps": payload}

    return {
        "url": payload.get("url"),
        "steps": payload.get("steps", []),
    }

def start_teaching_mode(url):
    """
    Opens the browser and injects a script to capture user interactions.
    Saves the captured steps (role, name/label, interaction_type) into workflow.json.
    """
    steps = []
    
    def handle_interaction(source, interaction_data):
        print(f"Captured interaction: {interaction_data}")
        steps.append(interaction_data)
        _save_workflow(url, steps)
            
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        # Expose binding to allow JS to send data back to Python
        page.expose_binding("captureInteraction", handle_interaction)
        
        # The JS injection looks at standard form elements
        injection_script = """
        document.addEventListener('change', (e) => {
            if(e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
                const label = e.target.labels && e.target.labels.length > 0 ? e.target.labels[0].innerText : e.target.name || e.target.id;
                const value = e.target.value;
                window.captureInteraction({
                    type: "input",
                    tag: e.target.tagName,
                    id: e.target.id,
                    name: e.target.name,
                    label: label,
                    value: value
                });
            }
        });
        document.addEventListener('click', (e) => {
            if(e.target.tagName === 'BUTTON' || (e.target.tagName === 'INPUT' && e.target.type === 'submit')) {
                const text = e.target.innerText || e.target.value;
                window.captureInteraction({
                    type: "click",
                    tag: e.target.tagName,
                    text: text
                });
            }
        });
        """
        page.add_init_script(injection_script)
        page.goto(url)
        
        print("Teaching mode active. Interact with the website. Close the browser when done.")
        
        # Keep alive until browser closes
        try:
            page.wait_for_event("close", timeout=0) # wait indefinitely
        except Exception:
            pass
        
        browser.close()
        _save_workflow(url, steps)
        print(f"Teaching mode finished. Saved {len(steps)} steps to {WORKFLOW_FILE}")


def run_execution_mode(url=None, override_data=None, headless=False):
    """
    Executes the workflow.json.
    override_data is a dict (e.g., {"SSN": "123-456", "First Name": "Alice"})
    that the LLM extracted from the user prompt. We use this to override saved values.
    Returns screenshot path.
    """
    if not override_data:
        override_data = {}

    try:
        workflow = load_workflow()
    except FileNotFoundError as exc:
        return False, str(exc), None

    steps = workflow["steps"]
    target_url = url or workflow["url"]
    if not target_url:
        return False, "No target URL found. Please provide a URL and record the workflow again.", None

    if not steps:
        return False, "Recorded workflow is empty. Please record the workflow again.", None

    # Normalize keys for override (e.g. lowercase matching)
    override_data_normalized = {k.lower().strip(): v for k, v in override_data.items()}

    screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", f"success_{int(time.time())}.png")
    os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            page.goto(target_url)
            page.wait_for_load_state("networkidle")

            for step in steps:
                if step["type"] == "input":
                    # Determine locator based on id, name, or label. Playwright get_by_label is preferred for semantic robustness.
                    locator = None
                    label = step.get("label", "").strip()
                    if label:
                        locator = page.get_by_label(label, exact=True)
                        if locator.count() == 0:
                           locator = page.get_by_label(label) # fuzzy

                    if not locator or locator.count() == 0:
                        if step.get("id"):
                            locator = page.locator(f"#{step['id']}")
                        elif step.get("name"):
                            locator = page.locator(f"[name='{step.get('name')}']")

                    # Decide what value to use
                    val_to_use = step.get("value", "")
                    # Try to match step's label or name to override_data keys
                    label_l = label.lower()
                    name_l = step.get("name", "").lower()
                    id_l = step.get("id", "").lower()

                    for ok, ov in override_data_normalized.items():
                        if ok in label_l or ok in name_l or ok in id_l:
                            val_to_use = ov
                            break
                    
                    if locator and locator.count() > 0:
                        locator.first.fill(val_to_use)
                    else:
                        print(f"Warning: could not find element for step: {step}")

                elif step["type"] == "click":
                    text = step.get("text", "").strip()
                    if text:
                        locator = page.get_by_role("button", name=text)
                        if locator.count() > 0:
                            locator.first.click()
                        else:
                             # fallback
                             page.locator("button, input[type='submit']").filter(has_text=text).first.click()
                    else:
                        page.locator("button, input[type='submit']").first.click()

            time.sleep(2) # brief pause to let UI react
            page.screenshot(path=screenshot_path, full_page=True)
            return True, "Execution complete", screenshot_path

        except Exception as e:
            page.screenshot(path=screenshot_path, full_page=True)
            return False, f"Execution failed: {str(e)}", screenshot_path
        finally:
            browser.close()

if __name__ == "__main__":
    # Test script locally
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "teach":
        start_teaching_mode("http://localhost:5000")
    elif len(sys.argv) > 1 and sys.argv[1] == "run":
        run_execution_mode("http://localhost:5000", {"ssn": "999-99-9999", "first": "John", "last": "Doe"}, headless=False)
