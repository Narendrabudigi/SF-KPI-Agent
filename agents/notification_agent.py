from rich.console import Console
from rich.panel import Panel

import config
from clients import notification_client

console = Console()

def _get_stakeholder_email(kpi_name: str, severity: str = None) -> str:
    """Routes to the correct stakeholder based on the HR KPI Domain."""
    kpi_lower = kpi_name.lower()
    
    email = None
    if "time-to-hire" in kpi_lower or "recruitment" in kpi_lower:
        email = config.TA_HEAD_EMAIL
    elif "onboarding" in kpi_lower or "offboarding" in kpi_lower:
        email = config.HRBP_EMAIL
    elif "attrition" in kpi_lower or "employee central" in kpi_lower:
        email = config.CHRO_EMAIL
    elif "payroll" in kpi_lower or "payment" in kpi_lower:
        # Critical payroll events escalate to CFO
        if severity and severity.lower() == "critical":
            email = config.CFO_EMAIL
        else:
            email = config.PAYROLL_MANAGER_EMAIL
            
    # Fallback to general contacts if specific stakeholder email is not set
    if not email:
        email = config.CHRO_EMAIL or config.SENDER_EMAIL or config.SMTP_USER
        
    return email

def _save_to_dashboard(data: dict, kpi_name: str):
    """Saves the output to a local JSON file for the web dashboard."""
    import json
    import os
    from datetime import datetime
    
    file_path = "dashboard_data.json"
    
    # Add metadata
    entry = {
        "id": str(datetime.now().timestamp()),
        "timestamp": datetime.now().isoformat(),
        "kpi_name": kpi_name,
        "data": data
    }
    
    history = []
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                history = json.load(f)
        except:
            pass
            
    # Prepend new entry
    history.insert(0, entry)
    
    # Keep only the last 50 alerts to prevent bloat
    history = history[:50]
    
    with open(file_path, "w") as f:
        json.dump(history, f, indent=4)

def _format_html_email(data: dict, kpi_name: str) -> str:
    """Formats the JSON recommendation into a readable HTML email."""
    actions_html = "".join([
        f"<tr><td>{a.get('action', '')}</td><td>{a.get('responsible_role', '')}</td><td>{a.get('sap_transaction', '')}</td><td>{a.get('effort', '')}</td></tr>"
        for a in data.get("immediate_actions", [])
    ])
    
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6;">
        <h2 style="color: #d9381e;">KPI Breach Alert: {kpi_name}</h2>
        
        <h3>Executive Summary</h3>
        <p>{data.get('executive_summary', '')}</p>
        
        <h3>Root Cause Analysis (Top 5 Drivers)</h3>
        <ul>
            {"".join([f"<li>{rc}</li>" for rc in (data.get('root_causes', []) if isinstance(data.get('root_causes'), list) else [data.get('root_causes', '')])])}
        </ul>
        
        <h3>⚡ Immediate Actions (Within 1 week)</h3>
        <table border="1" cellpadding="8" style="border-collapse: collapse; width: 100%;">
          <tr style="background-color: #f2f2f2;">
            <th>Action</th>
            <th>Role</th>
            <th>SAP T-Code</th>
            <th>Effort</th>
          </tr>
          {actions_html}
        </table>
        
        <h3 style="color: #d9381e;">⚠️ Risk Assessment</h3>
        <p>{data.get('risk_assessment', '')}</p>
      </body>
    </html>
    """
    return html

def _format_teams_markdown(data: dict, kpi_name: str) -> str:
    """Formats the JSON recommendation into Markdown for MS Teams."""
    md = f"## 🚨 KPI Breach Alert: {kpi_name}\n\n"
    md += f"**Executive Summary:** {data.get('executive_summary', '')}\n\n"
    root_causes = data.get("root_causes", [])
    if isinstance(root_causes, str):
        md += f"**Root Cause:** {root_causes}\n\n"
    else:
        md += "**Root Causes:**\n"
        for rc in root_causes:
            md += f"- {rc}\n"
        md += "\n"
    md += "### ⚡ Immediate Actions\n"
    
    for a in data.get("immediate_actions", []):
        md += f"- **{a.get('action', '')}** (Role: {a.get('responsible_role', '')} | SAP T-Code: {a.get('sap_transaction', '')})\n"
        
    md += f"\n**⚠️ Risk Assessment:** {data.get('risk_assessment', '')}"
    return md

def distribute_recommendations(data: dict, kpi_name: str, severity: str = "Critical", row: dict = None):
    """
    Takes the JSON recommendations from the LLM and distributes them to the
    Notification Layer (Email, Teams, SAP Action Center, Dashboard).
    
    Args:
        data: The LLM-generated prescriptive JSON recommendations
        kpi_name: The breached KPI name
        severity: Breach severity ('Critical' or 'Warning')
        row: The original OData SAP row dict (used to record breach in active_breaches.json)
    """
    console.print(Panel(f"[bold blue]Distributing Prescriptive Recommendations for: {kpi_name}[/bold blue]", border_style="blue"))
    
    stakeholder_email = _get_stakeholder_email(kpi_name, severity)
    subject = f"ACTION REQUIRED: AI Prescriptive Analysis for {kpi_name} Breach"
    
    # 1. Send Email via SMTP
    html_body = _format_html_email(data, kpi_name)
    notification_client.send_email(stakeholder_email, subject, html_body)
    
    # 2. Send MS Teams Notification
    markdown_body = _format_teams_markdown(data, kpi_name)
    notification_client.send_teams_message(markdown_body)
    
    # 3. Save to Dashboard
    _save_to_dashboard(data, kpi_name)
    
    # 4. Record active breach in breach_tracker so active_breaches.json is updated
    try:
        import breach_tracker
        recorded_id = breach_tracker.record_breach_notification(kpi_name, severity=severity, row=row)
        console.print(f"[bold green]📌 Recorded active breach: '{recorded_id}' in active_breaches.json[/bold green]")
    except Exception as e:
        console.print(f"[yellow]⚠️  Failed to update breach tracker: {e}[/yellow]")
    
    console.print("[bold green]✅ Notification Routing & Dashboard Update Complete![/bold green]")
