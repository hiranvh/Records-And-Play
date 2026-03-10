import os
import datetime

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

def generate_report(screenshot_path, db_record, ssn, match_status, error_msg=""):
    """
    Generates a simple HTML report demonstrating "Proof of Work".
    Returns the path to the HTML report.
    """
    if not os.path.exists(REPORTS_DIR):
        os.makedirs(REPORTS_DIR)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"report_{ssn}_{timestamp}.html"
    report_path = os.path.join(REPORTS_DIR, report_filename)
    
    status_color = "green" if match_status else "red"
    status_text = "MATCH" if match_status else "MISMATCH / FAILED"

    html_content = f"""
    <html>
    <head>
        <title>Proof of Work - {ssn}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .header {{ background-color: #333; color: white; padding: 10px; text-align: center; }}
            .status {{ font-size: 24px; font-weight: bold; color: {status_color}; text-align: center; margin: 20px 0; border: 2px solid {status_color}; padding: 10px; }}
            .container {{ display: flex; flex-direction: row; justify-content: space-around; }}
            .box {{ width: 45%; border: 1px solid #ccc; padding: 10px; }}
            img {{ max-width: 100%; height: auto; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>Automation Agent - Verification Report</h2>
            <p>Task: Create Customer (SSN: {ssn})</p>
            <p>Date: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
        
        <div class="status">{status_text}</div>
        {f"<p style='color:red; text-align:center;'>{error_msg}</p>" if error_msg else ""}
        
        <div class="container">
            <div class="box">
                <h3>1. System Database Record</h3>
                {"<p>No record found.</p>" if not db_record else ""}
                {generate_table(db_record) if db_record else ""}
            </div>
            <div class="box">
                <h3>2. Web UI Screenshot</h3>
                <img src="{screenshot_path}" alt="Success Screenshot" />
            </div>
        </div>
    </body>
    </html>
    """

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return report_path

def generate_table(record):
    rows = "".join([f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in record.items()])
    return f"<table>{rows}</table>"
