from rich.console import Console
from rich.panel import Panel
import re

import config
from clients import notification_client

console = Console()

def _convert_markdown_to_html_highlights(val):
    """Recursively converts markdown bold syntax (**text**) to HTML styled highlights."""
    if isinstance(val, str):
        return re.sub(
            r'\*\*(.*?)\*\*',
            r'<strong style="color: #3b82f6; font-weight: 600; background-color: rgba(59, 130, 246, 0.08); padding: 2px 4px; border-radius: 4px; font-family: inherit;">\1</strong>',
            val
        )
    elif isinstance(val, list):
        return [_convert_markdown_to_html_highlights(item) for item in val]
    elif isinstance(val, dict):
        return {k: _convert_markdown_to_html_highlights(v) for k, v in val.items()}
    return val


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
    immediate_html = "".join([
        f"<tr><td>{a.get('action', '')}</td><td>{a.get('responsible_role', '')}</td></tr>"
        for a in data.get("immediate_actions", [])
    ])
    
    preventative_html = "".join([
        f"<tr><td>{p.get('action', '')}</td><td>{p.get('responsible_role', '')}</td></tr>"
        for p in data.get("preventative_actions", [])
    ])
    
    predictive_list = "".join([
        f"<li>{pred}</li>"
        for pred in data.get("predictive_risk_analysis", [])
    ])
    
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #0f172a;">
        <h2 style="color: #f43f5e; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px;">KPI Breach Alert: {kpi_name}</h2>
        
        <h3 style="color: #3b82f6;">Executive Summary</h3>
        <p>{data.get('executive_summary', '')}</p>
        
        <h3 style="color: #3b82f6;">Root Causes</h3>
        <ul>
            {"".join([f"<li>{rc}</li>" for rc in (data.get('root_causes', []) if isinstance(data.get('root_causes'), list) else [data.get('root_causes', '')])])}
        </ul>
        
        <h3 style="color: #3b82f6;">⚡ Immediate Actions (Within 1 week)</h3>
        <table border="1" cellpadding="8" style="border-collapse: collapse; width: 100%; border: 1px solid #cbd5e1;">
          <tr style="background-color: #f8fafc; text-align: left;">
            <th>Action</th>
            <th>Role</th>
          </tr>
          {immediate_html or '<tr><td colspan="2" style="font-style: italic;">None recommended.</td></tr>'}
        </table>
        
        <h3 style="color: #3b82f6;">🔮 Predictive Risk Analysis</h3>
        <ul>
            {predictive_list or '<li>No risk predictions available.</li>'}
        </ul>
        
        <h3 style="color: #3b82f6;">🛡️ Preventative Actions</h3>
        <table border="1" cellpadding="8" style="border-collapse: collapse; width: 100%; border: 1px solid #cbd5e1;">
          <tr style="background-color: #f8fafc; text-align: left;">
            <th>Action</th>
            <th>Role</th>
          </tr>
          {preventative_html or '<tr><td colspan="2" style="font-style: italic;">None recommended.</td></tr>'}
        </table>
        
        <h3 style="color: #f43f5e; margin-top: 24px;">⚠️ Risk Assessment</h3>
        <div style="background-color: #fff1f2; border-left: 4px solid #f43f5e; padding: 12px 16px; border-radius: 4px; color: #9f1239;">
          {data.get('risk_assessment', '')}
        </div>
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
        md += f"- **{a.get('action', '')}** (Role: {a.get('responsible_role', '')})\n"
        
    md += "\n### 🔮 Predictive Risk Analysis\n"
    for p in data.get("predictive_risk_analysis", []):
        md += f"- {p}\n"
        
    md += "\n### 🛡️ Preventative Actions\n"
    for p in data.get("preventative_actions", []):
        md += f"- **{p.get('action', '')}** (Role: {p.get('responsible_role', '')})\n"
        
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
    
    # Convert markdown bolding to HTML highlights for Email and Dashboard formats
    html_data = _convert_markdown_to_html_highlights(data)
    
    # 1. Send Email via SMTP
    html_body = _format_html_email(html_data, kpi_name)
    notification_client.send_email(stakeholder_email, subject, html_body)
    
    # 2. Send MS Teams Notification
    markdown_body = _format_teams_markdown(data, kpi_name)
    notification_client.send_teams_message(markdown_body)
    
    # 3. Save to Dashboard
    _save_to_dashboard(html_data, kpi_name)
    
    # 4. Record active breach in breach_tracker so active_breaches.json is updated
    try:
        import breach_tracker
        recorded_id = breach_tracker.record_breach_notification(kpi_name, severity=severity, row=row)
        console.print(f"[bold green]📌 Recorded active breach: '{recorded_id}' in active_breaches.json[/bold green]")
    except Exception as e:
        console.print(f"[yellow]⚠️  Failed to update breach tracker: {e}[/yellow]")
    
    console.print("[bold green]✅ Notification Routing & Dashboard Update Complete![/bold green]")
