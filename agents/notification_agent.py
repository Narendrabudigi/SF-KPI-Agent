import os
import json
import re
from rich.console import Console
from rich.panel import Panel

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
    
    # Add metadata — include all breach IDs and enriched breach summaries
    breach_count = len(breach_summaries) if breach_summaries else 1
    breach_ids = [bs.get("kpi_id", "N/A") for bs in breach_summaries] if breach_summaries else []
    
    entry = {
        "id": str(datetime.now(timezone.utc).timestamp()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kpi_name": kpi_name,
        "breach_count": breach_count,
        "breach_ids": breach_ids,
        "breach_summaries": breach_summaries or [],
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
    
    try:
        with open(file_path, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        console.print(f"[bold red]Failed to save dashboard data:[/bold red] {e}")


def _build_dynamic_breach_table(breach_summaries: list, format_type: str = 'html') -> str:
    """
    Dynamically generates an Actual Breach breakdown table for HTML email or MS Teams Markdown.
    Checks which columns have at least one non-empty value across all breach summaries,
    and automatically omits columns that have no data.
    """
    if not breach_summaries:
        return ""

    # Sort summaries: CRITICAL first, then WARNING
    def sort_key(bs):
        sev = str(bs.get("severity", "")).upper()
        rank = 0 if sev == "CRITICAL" else (1 if sev == "WARNING" else 2)
        dept = str(bs.get("department") or bs.get("kpi_id") or "").upper()
        return (rank, dept)
        
    sorted_bs = sorted(breach_summaries, key=sort_key)

    # Check non-empty columns dynamically
    has_dept = any(bs.get("department") for bs in sorted_bs)
    has_loc = any(bs.get("location") for bs in sorted_bs)
    has_person = any(bs.get("person") and str(bs.get("person")).strip() not in ("0", "None", "") for bs in sorted_bs)
    has_stage = any(bs.get("stage") for bs in sorted_bs)
    has_why = any(bs.get("why") for bs in sorted_bs)
    has_sla = any(bs.get("sla_impact") for bs in sorted_bs)
    has_hr = any(bs.get("hr_lead") for bs in sorted_bs)

    if format_type == 'html':
        headers = []
        if has_dept or has_loc:
            headers.append("Department & Location")
        if has_person:
            headers.append("Candidate / Employee & ID")
        elif not has_dept and not has_loc:
            headers.append("KPI / ID")
        if has_stage:
            headers.append("Current Stage / Step")
        if has_why:
            headers.append("Why (Delay Driver / Bottleneck)")
        if has_sla:
            headers.append("SLA Impact")
        if has_hr:
            headers.append("HR Lead / Manager")
        headers.append("Severity & Resolution Priority")

        th_html = "".join([f'<th style="padding: 8px; border-bottom: 2px solid #fca5a5;">{h}</th>' for h in headers])

        rows_html = ""
        for bs in sorted_bs:
            sev = str(bs.get("severity", "Warning")).upper()
            is_crit = sev == "CRITICAL"
            sev_color = "#ef4444" if is_crit else "#f59e0b"
            action_tag = "🔥 URGENT (Resolve First)" if is_crit else "⚡ HIGH PRIORITY"
            sev_badge = f'<span style="background-color: {sev_color}; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 700;">{sev}</span><br><span style="font-size: 11px; color: {sev_color}; font-weight: 600;">{action_tag}</span>'
            row_bg = "#fff5f5" if is_crit else "#fffbeb"

            tds = []
            if has_dept or has_loc:
                dept_str = bs.get("department", "")
                loc_str = bs.get("location", "")
                if dept_str and loc_str:
                    val = f'<strong>{dept_str}</strong><br><small style="color: #64748b;">📍 {loc_str}</small>'
                elif dept_str:
                    val = f'<strong>{dept_str}</strong>'
                else:
                    val = f'<strong>{loc_str}</strong>'
                tds.append(f'<td style="padding: 10px; border-bottom: 1px solid #e2e8f0;">{val}</td>')

            if has_person:
                p_val = bs.get("person") or bs.get("kpi_id", "N/A")
                tds.append(f'<td style="padding: 10px; border-bottom: 1px solid #e2e8f0; font-weight: 600;">{p_val}</td>')
            else:
                k_val = bs.get("kpi_id", "N/A")
                tds.append(f'<td style="padding: 10px; border-bottom: 1px solid #e2e8f0; font-weight: 600;">{k_val}</td>')

            if has_stage:
                st_val = bs.get("stage") or "N/A"
                tds.append(f'<td style="padding: 10px; border-bottom: 1px solid #e2e8f0;"><span style="background-color: #e2e8f0; color: #334155; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600;">{st_val}</span></td>')

            if has_why:
                why_val = bs.get("why") or "SLA threshold breach"
                tds.append(f'<td style="padding: 10px; border-bottom: 1px solid #e2e8f0; font-size: 12px; color: #334155;">{why_val}</td>')

            if has_sla:
                sla_val = bs.get("sla_impact") or "N/A"
                tds.append(f'<td style="padding: 10px; border-bottom: 1px solid #e2e8f0; font-size: 12px; font-weight: 600; color: #dc2626;">{sla_val}</td>')

            if has_hr:
                hr_val = bs.get("hr_lead") or "N/A"
                tds.append(f'<td style="padding: 10px; border-bottom: 1px solid #e2e8f0; font-size: 12px;">{hr_val}</td>')

            tds.append(f'<td style="padding: 10px; border-bottom: 1px solid #e2e8f0;">{sev_badge}</td>')

            rows_html += f'<tr style="background-color: {row_bg};">{"".join(tds)}</tr>'

        return f"""
        <div style="background-color: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 16px; margin-bottom: 24px; overflow-x: auto;">
          <h3 style="color: #991b1b; margin: 0 0 6px 0;">📦 {len(sorted_bs)} Actual Breach(es) — Department, Location & Severity Breakdown</h3>
          <p style="margin: 0 0 12px 0; font-size: 13px; color: #7f1d1d;">Prioritized by severity (<strong>CRITICAL</strong> items listed first for urgent resolution).</p>
          <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
            <tr style="background-color: #fee2e2; text-align: left;">
              {th_html}
            </tr>
            {rows_html}
          </table>
        </div>
        """

    elif format_type == 'teams':
        headers = []
        if has_dept or has_loc:
            headers.append("Dept & Location")
        if has_person:
            headers.append("Employee / Candidate & ID")
        else:
            headers.append("KPI ID")
        if has_stage:
            headers.append("Current Stage")
        if has_why:
            headers.append("Why (Delay Driver)")
        if has_sla:
            headers.append("SLA Impact")
        if has_hr:
            headers.append("HR Lead")
        headers.append("Severity & Priority")

        md_header = "| " + " | ".join(headers) + " |\n"
        md_sep = "| " + " | ".join(["---"] * len(headers)) + " |\n"
        md_rows = ""

        for bs in sorted_bs:
            sev = str(bs.get("severity", "Warning")).upper()
            priority = "URGENT (Solve First)" if sev == "CRITICAL" else "HIGH PRIORITY"
            cols = []
            if has_dept or has_loc:
                dept_str = bs.get("department", "")
                loc_str = bs.get("location", "")
                cols.append(f"{dept_str} ({loc_str})" if dept_str and loc_str else (dept_str or loc_str))
            if has_person:
                cols.append(str(bs.get("person") or bs.get("kpi_id", "N/A")))
            else:
                cols.append(str(bs.get("kpi_id", "N/A")))
            if has_stage:
                cols.append(str(bs.get("stage", "N/A")))
            if has_why:
                cols.append(str(bs.get("why", "N/A")))
            if has_sla:
                cols.append(str(bs.get("sla_impact", "N/A")))
            if has_hr:
                cols.append(str(bs.get("hr_lead", "N/A")))
            cols.append(f"**{sev}** ({priority})")

            md_rows += "| " + " | ".join(cols) + " |\n"

        return f"**📦 {len(sorted_bs)} Actual Breaches Breakdown:**\n\n{md_header}{md_sep}{md_rows}\n"

    return ""


def _format_html_email(data: dict, kpi_name: str, breach_summaries: list = None, attachments: list = None) -> str:
    """Formats the JSON recommendation into a rich HTML email with dynamic breach breakdown table."""
    breach_count = len(breach_summaries) if breach_summaries else 1
    
    # Build dynamic breach breakdown table
    breach_header_html = _build_dynamic_breach_table(breach_summaries, format_type='html')

    # Build Excel attachment notice banner if attachments exist
    attachment_notice_html = ""
    if attachments:
        file_names = ", ".join([f"<code>{os.path.basename(f)}</code>" for f in attachments])
        attachment_notice_html = f"""
        <div style="background-color: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 8px; padding: 14px 16px; margin-bottom: 24px;">
          <h3 style="color: #065f46; margin: 0 0 6px 0;">📊 Critical Predicted Risks Export Attached</h3>
          <p style="margin: 0; font-size: 13px; color: #047857;">
            An Excel file ({file_names}) containing all <strong>CRITICAL Predicted Risk</strong> records from Databricks ML results is attached to this email for operational review.
          </p>
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
        
        {attachment_notice_html}
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

def _format_teams_markdown(data: dict, kpi_name: str, breach_summaries: list = None, attachments: list = None) -> str:
    """Formats the JSON recommendation into Markdown for MS Teams."""
    breach_count = len(breach_summaries) if breach_summaries else 1
    title_suffix = f" — {breach_count} Breach(es)" if breach_count > 1 else ""
    
    md = f"## 🚨 KPI Breach Alert: {kpi_name}{title_suffix}\n\n"
    
    if attachments:
        file_names = ", ".join([os.path.basename(f) for f in attachments])
        md += f"**📊 Critical Predicted Risks Export Attached:** `{file_names}`\n\n"

    # Add dynamic breach summary table
    if breach_summaries:
        md += _build_dynamic_breach_table(breach_summaries, format_type='teams')
    
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

def distribute_recommendations(
    data: dict, 
    kpi_name: str, 
    severity: str = "Critical", 
    row: dict = None, 
    breach_summaries: list = None,
    attachments: list = None
):
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
        attachments: List of file attachment paths (e.g. Critical Predicted Risks Excel export)
    """
    breach_count = len(breach_summaries) if breach_summaries else 1
    console.print(Panel(
        f"[bold blue]Distributing Consolidated Prescriptive Recommendations for: {kpi_name} ({breach_count} breach(es))[/bold blue]"
        f"\n[dim]Attachments: {len(attachments) if attachments else 0} file(s)[/dim]",
        border_style="blue"
    ))
    
    stakeholder_email = _get_stakeholder_email(kpi_name, severity)
    
    # Subject line reflects batch count and critical predictive risks if attached
    if attachments:
        subject = f"ACTION REQUIRED: AI Prescriptive Analysis & Critical Predicted Risks Export — {kpi_name}"
    elif breach_count > 1:
        subject = f"ACTION REQUIRED: AI Prescriptive Analysis — {breach_count} {kpi_name} Breach(es) Detected"
    else:
        subject = f"ACTION REQUIRED: AI Prescriptive Analysis for {kpi_name} Breach"
    
    # Convert markdown bolding to HTML highlights for Email and Dashboard formats
    html_data = _convert_markdown_to_html_highlights(data)
    
    # 1. Send SINGLE consolidated Email via SMTP (with optional Excel attachment)
    html_body = _format_html_email(html_data, kpi_name, breach_summaries=breach_summaries, attachments=attachments)
    notification_client.send_email(stakeholder_email, subject, html_body, attachments=attachments)
    
    # 2. Send SINGLE consolidated MS Teams Notification
    markdown_body = _format_teams_markdown(data, kpi_name, breach_summaries=breach_summaries, attachments=attachments)
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

