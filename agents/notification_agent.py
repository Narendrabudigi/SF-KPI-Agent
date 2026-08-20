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

def _save_to_dashboard(data: dict, kpi_name: str, breach_summaries: list = None):
    """Saves the output to a local JSON file for the web dashboard."""
    import json
    import os
    from datetime import datetime, timezone
    
    file_path = "dashboard_data.json"
    
    # Add metadata — include all breach IDs in a single entry for batch mode
    breach_count = len(breach_summaries) if breach_summaries else 1
    breach_ids = [bs.get("kpi_id", "N/A") for bs in breach_summaries] if breach_summaries else []
    
    entry = {
        "id": str(datetime.now(timezone.utc).timestamp()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kpi_name": kpi_name,
        "breach_count": breach_count,
        "breach_ids": breach_ids,
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

def _format_html_email(data: dict, kpi_name: str, breach_summaries: list = None) -> str:
    """
    Formats the JSON recommendation into a readable HTML email.
    
    In batch mode (multiple breach_summaries), the email is consolidated:
    - Header shows all breach IDs and their severities
    - Single body covering all breaches holistically
    
    Args:
        data: LLM-generated prescriptive JSON recommendations
        kpi_name: The primary KPI name
        breach_summaries: List of breach summary dicts for consolidated header, e.g.
            [{"kpi_id": "OFB100002", "kpi_name": "...", "severity": "Warning"}, ...]
    """
    breach_count = len(breach_summaries) if breach_summaries else 1
    
    # Build the breach summary table for the email header (batch mode)
    breach_header_html = ""
    if breach_summaries and len(breach_summaries) > 1:
        breach_rows_html = ""
        is_attrition = "attrition" in kpi_name.lower() or any(bs.get("department") for bs in breach_summaries)
        id_col_header = "Department" if is_attrition else "KPI ID"
        
        for bs in breach_summaries:
            sev = bs.get("severity", "Unknown")
            sev_color = "#ef4444" if sev.lower() == "critical" else "#f59e0b"
            sev_badge = f'<span style="background-color: {sev_color}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 600;">{sev.upper()}</span>'
            display_id = bs.get("department") or bs.get("kpi_id", "N/A")
            breach_rows_html += f'<tr><td style="padding: 8px; border-bottom: 1px solid #e2e8f0;">{display_id}</td><td style="padding: 8px; border-bottom: 1px solid #e2e8f0;">{bs.get("kpi_name", kpi_name)}</td><td style="padding: 8px; border-bottom: 1px solid #e2e8f0;">{sev_badge}</td></tr>'
        
        breach_header_html = f"""
        <div style="background-color: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 16px; margin-bottom: 24px;">
          <h3 style="color: #991b1b; margin: 0 0 12px 0;">📦 {breach_count} Breach(es) Detected — Consolidated Report</h3>
          <table style="width: 100%; border-collapse: collapse;">
            <tr style="background-color: #fee2e2; text-align: left;">
              <th style="padding: 8px; border-bottom: 2px solid #fca5a5;">{id_col_header}</th>
              <th style="padding: 8px; border-bottom: 2px solid #fca5a5;">KPI Name</th>
              <th style="padding: 8px; border-bottom: 2px solid #fca5a5;">Severity</th>
            </tr>
            {breach_rows_html}
          </table>
        </div>
        """
    
    immediate_html = "".join([
        f"<tr><td>{a.get('action', '')}</td><td>{a.get('responsible_role', '')}</td></tr>"
        for a in data.get("immediate_actions", [])
    ])
    
    preventative_html = "".join([
        f"<tr><td>{p.get('action', '')}</td><td>{p.get('responsible_role', '')}</td></tr>"
        for p in data.get("preventative_actions", [])
    ])
    
    predictive_section_html = ""
    if data.get("predictive_risk_analysis"):
        predictive_list = "".join([
            f"<li>{pred}</li>"
            for pred in data.get("predictive_risk_analysis", [])
        ])
        predictive_section_html = f"""
        <h3 style="color: #3b82f6;">🔮 Predictive Risk Analysis</h3>
        <ul>
            {predictive_list}
        </ul>
        """
    
    # Title: show breach count if multiple
    title_suffix = f" — {breach_count} Breach(es)" if breach_count > 1 else ""
    
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #0f172a;">
        <h2 style="color: #f43f5e; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px;">KPI Breach Alert: {kpi_name}{title_suffix}</h2>
        
        {breach_header_html}
        
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
        
        {predictive_section_html}
        
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

def _format_teams_markdown(data: dict, kpi_name: str, breach_summaries: list = None) -> str:
    """Formats the JSON recommendation into Markdown for MS Teams."""
    breach_count = len(breach_summaries) if breach_summaries else 1
    title_suffix = f" — {breach_count} Breach(es)" if breach_count > 1 else ""
    
    md = f"## 🚨 KPI Breach Alert: {kpi_name}{title_suffix}\n\n"
    
    # Add breach summary table for batch mode
    if breach_summaries and len(breach_summaries) > 1:
        is_attrition = "attrition" in kpi_name.lower() or any(bs.get("department") for bs in breach_summaries)
        id_col = "Department" if is_attrition else "KPI ID"
        md += f"**📦 {breach_count} Breaches Detected:**\n\n"
        md += f"| {id_col} | Severity |\n|--------|----------|\n"
        for bs in breach_summaries:
            display_id = bs.get("department") or bs.get("kpi_id", "N/A")
            md += f"| {display_id} | {bs.get('severity', 'N/A')} |\n"
        md += "\n"
    
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
        
    if data.get("predictive_risk_analysis"):
        md += "\n### 🔮 Predictive Risk Analysis\n"
        for p in data["predictive_risk_analysis"]:
            md += f"- {p}\n"
        
    md += "\n### 🛡️ Preventative Actions\n"
    for p in data.get("preventative_actions", []):
        md += f"- **{p.get('action', '')}** (Role: {p.get('responsible_role', '')})\n"
        
    md += f"\n**⚠️ Risk Assessment:** {data.get('risk_assessment', '')}"
    return md

def distribute_recommendations(data: dict, kpi_name: str, severity: str = "Critical", row: dict = None, breach_summaries: list = None):
    """
    Takes the JSON recommendations from the LLM and distributes them to the
    Notification Layer (Email, Teams, SAP Action Center, Dashboard).
    
    In batch mode, sends a SINGLE CONSOLIDATED notification covering all breaches.
    
    Args:
        data: The LLM-generated prescriptive JSON recommendations
        kpi_name: The breached KPI name
        severity: Breach severity ('Critical' or 'Warning')
        row: The original OData SAP row dict or list of dicts (used to record breach in active_breaches.json)
        breach_summaries: List of breach summary dicts for consolidated notification
    """
    breach_count = len(breach_summaries) if breach_summaries else 1
    console.print(Panel(
        f"[bold blue]Distributing Consolidated Prescriptive Recommendations for: {kpi_name} ({breach_count} breach(es))[/bold blue]",
        border_style="blue"
    ))
    
    stakeholder_email = _get_stakeholder_email(kpi_name, severity)
    
    # Subject line reflects batch count
    if breach_count > 1:
        subject = f"ACTION REQUIRED: AI Prescriptive Analysis — {breach_count} {kpi_name} Breach(es) Detected"
    else:
        subject = f"ACTION REQUIRED: AI Prescriptive Analysis for {kpi_name} Breach"
    
    # Convert markdown bolding to HTML highlights for Email and Dashboard formats
    html_data = _convert_markdown_to_html_highlights(data)
    
    # 1. Send SINGLE consolidated Email via SMTP
    html_body = _format_html_email(html_data, kpi_name, breach_summaries=breach_summaries)
    notification_client.send_email(stakeholder_email, subject, html_body)
    
    # 2. Send SINGLE consolidated MS Teams Notification
    markdown_body = _format_teams_markdown(data, kpi_name, breach_summaries=breach_summaries)
    notification_client.send_teams_message(markdown_body)
    
    # 3. Save to Dashboard — single consolidated entry
    _save_to_dashboard(html_data, kpi_name, breach_summaries=breach_summaries)
    
    # 4. Record ALL individual breaches in breach_tracker so active_breaches.json is updated
    try:
        import breach_tracker
        if breach_summaries:
            # Batch mode — record each breach individually
            for bs in breach_summaries:
                # Build a minimal row dict for breach tracking
                if bs.get("department"):
                    row_for_tracking = {"Department": bs.get("department")}
                elif bs.get("kpi_id"):
                    row_for_tracking = {"KPI_ID": bs.get("kpi_id")}
                else:
                    row_for_tracking = None
                recorded_id = breach_tracker.record_breach_notification(
                    kpi_name, 
                    severity=bs.get("severity", severity), 
                    row=row_for_tracking
                )
                console.print(f"[bold green]📌 Recorded active breach: '{recorded_id}' in active_breaches.json[/bold green]")
        elif row:
            # Single breach (backward compat)
            if isinstance(row, list):
                for r in row:
                    recorded_id = breach_tracker.record_breach_notification(kpi_name, severity=severity, row=r)
                    console.print(f"[bold green]📌 Recorded active breach: '{recorded_id}' in active_breaches.json[/bold green]")
            else:
                recorded_id = breach_tracker.record_breach_notification(kpi_name, severity=severity, row=row)
                console.print(f"[bold green]📌 Recorded active breach: '{recorded_id}' in active_breaches.json[/bold green]")
    except Exception as e:
        console.print(f"[yellow]⚠️  Failed to update breach tracker: {e}[/yellow]")
    
    console.print(f"[bold green]✅ Consolidated Notification Routing & Dashboard Update Complete! ({breach_count} breach(es))[/bold green]")
