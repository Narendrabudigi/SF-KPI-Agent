import json
import re
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from clients import llm_client
from agents import notification_agent

console = Console()

SYSTEM_PROMPT = """You are an expert SAP SuccessFactors HR analyst specializing in Recruitment, Onboarding, Offboarding, Employee Central, and Employee Central Payroll operations. 
Your role is to analyze HR KPI breach root causes and provide actionable, prescriptive recommendations for the PRIMARY incident.

IMPORTANT: You may be given data for MULTIPLE breaches at once. In this case, provide a UNIFIED analysis covering ALL breaches together — do not repeat the structure for each breach separately.

Guidelines:
1. Focus your actionable recommendations on resolving the PRIMARY BREACH(ES).
2. DOWNSTREAM PREDICTIVE RISK & TARGET RESOLUTION TIMELINE:
    * In "predictive_risk_analysis", provide 2 to 3 detailed, concrete bullet points focusing EXCLUSIVELY on the downstream process listed under "Downstream Dependent HR Processes".
    * For EACH bullet point, you MUST provide:
        a) The specific operational bottleneck and impact on the downstream process (e.g. IT provisioning delay, compliance document verification hold, or new-hire training cohort postponement).
        b) The EXACT resolution timeframe / SLA in days required to prevent a breach in that downstream process (e.g., "Must be resolved within **4 business days** to prevent a cascading breach in **Onboarding Completion Time**").
    * NEVER mention or write predictive points for any other KPI or unrelated area. All bullet points in this section must focus 100% on different angles of the single listed downstream dependent process.
    * If "Downstream Dependent HR Processes" is not listed or empty (such as for Offboarding), you MUST return an empty array `[]` for "predictive_risk_analysis".
3. Be specific, extremely detailed, and actionable - avoid generic advice.
4. Consider SAP SuccessFactors capabilities in your recommendations.
5. Address both immediate fixes and long-term improvements to reduce future failures and support business development.
6. Quantify expected impact where possible.
7. Consider cross-functional HR impacts.
8. Prioritize recommendations by effort vs. impact.
9. ANALYZE DETAIL DATA: Carefully inspect the detailed employee or candidate level data provided in the root table within the root cause analysis findings. Detail exactly WHY the breach occurred, and BECAUSE OF WHOM (identifying the specific candidate IDs, employee IDs, department names, or responsible owners who caused/are experiencing the delay) ONLY IF they are explicitly present in the provided OData row or root cause details. If no specific record is affected (e.g., in a data-quality breach), explain that this is a system/data-quality issue, mentioning file/ID info (like "July 2026 CSV source data" or "KPI ID: OFF400013") rather than leaving it empty.
10. HIGHLIGHT IMPORTANT THINGS: In your descriptions, executive summaries, root causes, and precautions, highlight critical values, specific names, employee IDs, percentages, and durations using markdown bolding (e.g., **Engineering**, **EMP100001**, **45.6%**, **4 days**). Only highlight real values present in the input.
11. DETAILED EXPLANATION: Write detailed, context-rich analysis and recommendations rather than brief summaries. Provide deep business insights that help in process optimization.
12. NO "MODEL" OR "ML" REFERENCES: Speak authoritatively as a senior HR analyst. Absolutely DO NOT say "the model predicted", "the ML model says", "our algorithm shows", etc. State the insights directly as business facts or forecasts (e.g., "This delay will compress the onboarding timeline by 8 days" instead of "The model predicts an 8 day delay"). Never use technical model names.
13. NO RECORD HALLUCINATIONS: Do not invent specific delayed records for dependent or correlated KPIs that are department-level or do not have matching record-level details in the input context. Keep "affected_records" empty or strictly limited to actual records provided in the inputs.
14. FLUID EXECUTIVE NARRATIVE: In your Executive Summary and Root Causes, write in smooth, professional, fluid business paragraphs. Do NOT output raw prompt bullet labels or literal headers such as "* Why it is late:", "* By how much:", or "* What is the downstream delay:". Integrate the operational bottleneck, total delay duration, and downstream impacts seamlessly into natural C-suite sentences.
15. C-SUITE FORMATTING RULES (FOR CEO/CFO):
    *   **Convert Day of Week Codes**: Never output raw codes like `Dayofweek: 4.0` or `Dayofweek: 2.0`. Translate them to actual days of the week (e.g., **Thursday**, **Tuesday**).
    *   **Translate Confidence Scores**: Convert raw confidence percentages into qualitative executive risk descriptors:
        *   90% - 100%: **Near Certain / Highly Critical**
        *   70% - 89%: **High Probability Risk**
        *   50% - 69%: **Moderate Risk / Monitor**
    *   **No Technical placeholders**: Clean up any database artifacts like `nan`, `null`, or raw database keys from the final text.
16. MULTIPLE BREACHES: When multiple breach records are provided, your executive summary should mention ALL breach IDs and their severities. Your root causes should cover the common and individual drivers across all breaches. Recommendations should address ALL breaches holistically.
17. PROFESSIONAL BUSINESS TONE & NO INTERNAL LABELS: Never output internal prompt labels, technical placeholders, emojis (like 🔴, 🟡, 🔵), or phrases like "according to the rules", "interdependency matrix", or "based on the rules". Speak strictly in natural, professional executive HR terminology.
18. DEPARTMENT-WISE & SEVERITY-WISE PRIORITIZATION (ACTUAL BREACHES): When presenting root cause analysis, executive summaries, and immediate actions for actual breaches, structure your analysis Department-Wise first, and within each department, sort/order breaches by Severity (**CRITICAL** items prioritized at the top before **WARNING** items). Clearly highlight which department requires immediate attention and which specific breaches must be resolved faster.

Format your response as valid JSON with the following structure:
{
    "executive_summary": "Clean, high-level summary of the breach(es), their drivers, and downstream operational impact. Grouped department-wise and sorted by severity (Critical first, then Warning). Highlight key records and percentages.",
    "root_causes": [
        "Detail driver 1 (explain what happened and why in plain business terms)...",
        "Detail driver 2...",
        "Detail driver 3..."
    ],
    "immediate_actions": [
        {
            "action": "Immediate tactical task to implement within 1 week...",
            "responsible_role": "Responsible person or role (e.g., Recruiter EMP101621 / hiring manager EMP104419)..."
        }
    ],
    "predictive_risk_analysis": [
        "Downstream operational bottleneck 1 on the affected KPI with an explicit resolution timeline (e.g., 'Delay in background verification directly compresses pre-onboarding runway for **Onboarding Completion Time**; hiring managers must clear pending approvals within **3 business days** to prevent a downstream onboarding breach.').",
        "Downstream operational bottleneck 2 on the SAME affected KPI with an explicit resolution timeline (e.g., 'Hardware and system access provisioning lead times are at risk; IT requests must be submitted within **5 business days** to ensure day-one readiness and avoid an onboarding milestone delay.')."
    ],
    "preventative_actions": [
        {
            "action": "Medium/long-term policy or configuration change to prevent future breaches...",
            "responsible_role": "Responsible person/role..."
        }
    ],
    "risk_assessment": "Plain-language business warning explaining the long-term impact on operations, company baseline, and other departments if left unaddressed."
}
"""

def _generate_critical_risks_excel(critical_risks: list, kpi_name: str) -> str:
    """
    Generates an Excel file (.xlsx) or CSV containing all CRITICAL predicted risk records.
    Returns the absolute path to the generated file.
    """
    import pandas as pd
    import os
    import re
    import tempfile
    
    if not critical_risks:
        return None
        
    try:
        df = pd.DataFrame(critical_risks)
        safe_kpi = re.sub(r'[^a-zA-Z0-9_]', '_', kpi_name)
        filename = f"Critical_Predicted_Risks_{safe_kpi}.xlsx"
        temp_dir = tempfile.gettempdir()
        filepath = os.path.join(temp_dir, filename)
        
        try:
            df.to_excel(filepath, index=False, engine='openpyxl')
        except Exception:
            filename = f"Critical_Predicted_Risks_{safe_kpi}.csv"
            filepath = os.path.join(temp_dir, filename)
            df.to_csv(filepath, index=False)
            
        console.print(f"[bold green]📊 Generated temp attachment export for {len(critical_risks)} Critical Predicted Risk record(s): {filepath}[/bold green]")
        return filepath
    except Exception as e:
        console.print(f"[bold red]Failed to generate export for critical predicted risks: {e}[/bold red]")
        return None

def _clean_json_response(raw_response: str) -> str:
    """Removes markdown code blocks if the LLM returned them"""
    cleaned = raw_response.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
        
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
        
    return cleaned.strip()

def _clean_summary_text(text: str) -> str:
    """Sanitizes raw prompt headers like '* Why it is late:' from executive summary into clean prose."""
    if not isinstance(text, str):
        return text
    text = re.sub(r'[*•]\s*\*\*Why it is late\*\*\s*:\s*', 'Why it is late: ', text, flags=re.IGNORECASE)
    text = re.sub(r'[*•]\s*\*\*By how much\*\*\s*:\s*', 'By how much: ', text, flags=re.IGNORECASE)
    text = re.sub(r'[*•]\s*\*\*What is the downstream delay\*\*\s*:\s*', 'Downstream delay: ', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*\*\s*Why it is late:\s*', ' Why it is late: ', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*\*\s*By how much:\s*', ' By how much: ', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*\*\s*What is the downstream delay:\s*', ' Downstream delay: ', text, flags=re.IGNORECASE)
    return text.strip()


def _robust_json_parse(raw_response: str) -> dict:
    """
    Robustly parses JSON from LLM response.
    Handles trailing duplicate words, unescaped characters, or formatting glitches.
    """
    cleaned = _clean_json_response(raw_response)
    data = None
    
    # 1. Direct standard parse
    try:
        data = json.loads(cleaned)
    except Exception:
        pass
        
    # 2. Extract substring between first { and last }
    if not data:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            candidate = cleaned[start:end+1]
            try:
                data = json.loads(candidate)
            except Exception:
                pass
                
            if not data:
                candidate_fixed = re.sub(r'("\s*)\n[^\{\}\[\]",:]+\s*\}', r'\1\n}', candidate)
                try:
                    data = json.loads(candidate_fixed)
                except Exception:
                    pass

    if data and isinstance(data, dict):
        if "executive_summary" in data:
            data["executive_summary"] = _clean_summary_text(data["executive_summary"])
        return data

    # 4. Fallback: Regex extraction of structured fields
    data = {}
    
    # Extract executive_summary
    m_exec = re.search(r'"executive_summary"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned, re.DOTALL)
    if m_exec:
        try:
            data["executive_summary"] = bytes(m_exec.group(1), "utf-8").decode("unicode_escape")
        except Exception:
            data["executive_summary"] = m_exec.group(1)
    else:
        data["executive_summary"] = "Executive summary generated for KPI breach."

    # Extract risk_assessment
    m_risk = re.search(r'"risk_assessment"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned, re.DOTALL)
    if m_risk:
        try:
            data["risk_assessment"] = bytes(m_risk.group(1), "utf-8").decode("unicode_escape")
        except Exception:
            data["risk_assessment"] = m_risk.group(1)
    else:
        data["risk_assessment"] = ""

    # Extract array of strings: root_causes, predictive_risk_analysis
    for arr_key in ["root_causes", "predictive_risk_analysis"]:
        m_arr = re.search(rf'"{arr_key}"\s*:\s*\[(.*?)\]', cleaned, re.DOTALL)
        if m_arr:
            items = re.findall(r'"((?:[^"\\]|\\.)*)"', m_arr.group(1))
            extracted = []
            for it in items:
                try:
                    extracted.append(bytes(it, "utf-8").decode("unicode_escape"))
                except Exception:
                    extracted.append(it)
            data[arr_key] = extracted
        else:
            data[arr_key] = []

    # Extract array of objects: immediate_actions, preventative_actions
    for obj_arr_key in ["immediate_actions", "preventative_actions"]:
        m_arr = re.search(rf'"{obj_arr_key}"\s*:\s*\[(.*?)\]\s*(?:,|\n|\}})', cleaned, re.DOTALL)
        if m_arr:
            obj_blocks = re.findall(r'\{(.*?)\}', m_arr.group(1), re.DOTALL)
            actions = []
            for b in obj_blocks:
                m_act = re.search(r'"action"\s*:\s*"((?:[^"\\]|\\.)*)"', b, re.DOTALL)
                m_role = re.search(r'"responsible_role"\s*:\s*"((?:[^"\\]|\\.)*)"', b, re.DOTALL)
                if m_act:
                    act_str = m_act.group(1)
                    role_str = m_role.group(1) if m_role else "HR Operations"
                    try:
                        act_str = bytes(act_str, "utf-8").decode("unicode_escape")
                    except Exception:
                        pass
                    try:
                        role_str = bytes(role_str, "utf-8").decode("unicode_escape")
                    except Exception:
                        pass
                    actions.append({
                        "action": act_str,
                        "responsible_role": role_str
                    })
            data[obj_arr_key] = actions
        else:
            data[obj_arr_key] = []

    if data.get("executive_summary") or data.get("root_causes") or data.get("immediate_actions"):
        return data

    raise ValueError("Could not parse or extract valid JSON from LLM output.")

def run_prescriptive_analysis(
    incident_id: str,
    kpi_name: str,
    interdependency_info: dict = None,
    correlated_with: list = None,
    severity: str = "Critical",
    row: dict = None,
    breach_summaries: list = None,
    critical_predictive_risks: list = None
):
    """
    Takes the incident ID, retrieves the ML findings context from the MCP memory,
    and passes them to the LLM to generate prescriptive business recommendations.
    
    In batch mode, the context contains ONLY ACTUAL_BREACH data (PREDICTIVE_RISK is excluded).
    Critical PREDICTIVE_RISK records are exported to an Excel attachment.
    A single consolidated email is sent covering all breaches.
    
    Args:
        incident_id: Unique breach incident key used to retrieve context from MCP memory
        kpi_name: The primary breached KPI name
        interdependency_info: Full dictionary mapping from INTERDEPENDENCY_MATRIX (affects, affected_by, correlated_with)
        correlated_with: List of historically correlated KPI names (backward compat)
        severity: Breach severity ('Critical' or 'Warning') — forwarded to notification routing
        row: The original OData SAP row dict (or list of dicts in batch mode)
        breach_summaries: List of breach summary dicts for consolidated notification
        critical_predictive_risks: List of CRITICAL predicted risk records from Databricks ML results
    """
    from clients.mcp_client import mcp_client
    ml_context = mcp_client.call_tool("get_incident_context", {"incident_id": incident_id})
    
    # Generate Excel export if CRITICAL predicted risk records exist
    attachments = []
    if critical_predictive_risks:
        excel_file = _generate_critical_risks_excel(critical_predictive_risks, kpi_name)
        if excel_file:
            attachments.append(excel_file)

    # Determine breach count for logging
    breach_count = len(breach_summaries) if breach_summaries else 1
    console.print(Panel(
        f"[bold magenta]Starting Prescriptive Analysis for {breach_count} Breach(es): {kpi_name}...[/bold magenta]"
        f"\n[dim]Note: Only ACTUAL_BREACH data is included (PREDICTIVE_RISK excluded from prompt; {len(critical_predictive_risks) if critical_predictive_risks else 0} Critical risks exported to Excel)[/dim]",
        border_style="magenta"
    ))
    
    # Retrieve the original OData SAP row(s) — may be a list of rows in batch mode
    odata_row_str = mcp_client.call_tool("get_odata_row", {"incident_id": incident_id})
    odata_context = ""
    if odata_row_str and "No OData row found" not in odata_row_str:
        try:
            import json as _json
            odata_data = _json.loads(odata_row_str)
            
            if isinstance(odata_data, list):
                # Batch mode — multiple OData rows
                odata_context = (
                    f"\n\n--- ORIGINAL SAP OData BREACH ROWS ({len(odata_data)} records) ---\n"
                    f"(Employee/Candidate Level Detail from SuccessFactors — use these to identify WHO caused the breaches)\n"
                    f"{_json.dumps(odata_data, indent=2)}\n"
                )
                console.print(f"[dim]📎 OData batch context injected into LLM prompt ({len(odata_data)} rows)[/dim]")
            else:
                # Single row (backward compat)
                odata_context = (
                    f"\n\n--- ORIGINAL SAP OData BREACH ROW ---\n"
                    f"(Employee/Candidate Level Detail from SuccessFactors — use this to identify WHO caused the breach)\n"
                    f"{_json.dumps(odata_data, indent=2)}\n"
                )
                console.print(f"[dim]📎 OData row context injected into LLM prompt ({len(odata_data)} fields)[/dim]")
        except Exception:
            odata_context = f"\n\nSAP OData Row (raw): {odata_row_str}\n"

    # Build Interdependency Context from INTERDEPENDENCY_MATRIX (affects only for predictive risk)
    interdependency_text = ""
    affects_list = []
    if interdependency_info and isinstance(interdependency_info, dict):
        affects_list = interdependency_info.get("affects", [])
        affected_by_list = interdependency_info.get("affected_by", [])
        
        matrix_lines = []
        if affects_list:
            matrix_lines.append(f"- Downstream Dependent HR Processes (Predictive Risk Focus): {', '.join(affects_list)}")
            
        if affected_by_list:
            matrix_lines.append(f"- Preceding Influencing HR Processes (Root Cause Context): {', '.join(affected_by_list)}")
            
        if matrix_lines:
            interdependency_text = (
                f"\n\n--- HR PROCESS FLOW & DEPENDENCIES ---\n"
                f"Use this process flow to assess downstream operational impact:\n"
                + "\n".join(matrix_lines) + "\n"
            )
    
    # Add breach summary info to prompt if in batch mode
    breach_info_text = ""
    if breach_summaries and len(breach_summaries) > 1:
        breach_info_text = f"\n\nIMPORTANT: You are analyzing {len(breach_summaries)} ACTUAL BREACHES in this batch:\n"
        for bs in breach_summaries:
            if bs.get("department"):
                period_str = f" | Period: {bs.get('year')}-{bs.get('month')}" if bs.get('year') and bs.get('month') else ""
                breach_info_text += f"  - Department: {bs.get('department')} | Severity: {bs.get('severity', 'N/A')}{period_str}\n"
            else:
                breach_info_text += f"  - KPI_ID: {bs.get('kpi_id', 'N/A')} | Severity: {bs.get('severity', 'N/A')}\n"
        breach_info_text += "Provide a UNIFIED analysis covering ALL of these breaches, structured Department-Wise and sorted Severity-Wise (CRITICAL first).\n"
        
    # Construct User Prompt — includes Databricks ML results + OData row detail + Interdependency matrix context
    user_prompt = f"""Based on the following ML Root Cause Findings (ACTUAL BREACH data only — predictive risks have been excluded), provide your prescriptive recommendations to solve the breach(es) of {kpi_name}:
{breach_info_text}
{interdependency_text}
{odata_context}
{ml_context}
"""
    
    # Call the centralized LLM client (which handles fallbacks internally)
    raw_response = llm_client.generate_recommendation(SYSTEM_PROMPT, user_prompt)
    
    if not raw_response:
        console.print("[bold red]Failed to generate recommendations from any LLM provider.[/bold red]")
        return
        
    # Clean and parse the response with robust multi-stage repair
    try:
        data = _robust_json_parse(raw_response)
        
        # Enforce Interdependency Matrix rule: If there are NO downstream 'affects' KPIs, predictive_risk_analysis MUST be empty []
        if not affects_list:
            data["predictive_risk_analysis"] = []
        
        # Display Executive Summary
        console.print(Panel(data.get("executive_summary", ""), title=f"📊 Executive Summary ({breach_count} Breach(es))", border_style="green"))
        
        # Display Narrative
        root_causes = data.get('root_causes', [])
        if isinstance(root_causes, str):
            narrative = root_causes
        else:
            narrative = "\n".join([f"- {rc}" for rc in root_causes])
            
        console.print(f"\n[bold underline]Root Cause Narrative:[/bold underline]\n{narrative}\n")
        
        # Display Immediate Actions as a Table
        actions_table = Table(title="⚡ Immediate Actions (Within 1 week)", show_header=True, header_style="bold cyan")
        actions_table.add_column("Action")
        actions_table.add_column("Role")
      
        for action in data.get("immediate_actions", []):
            actions_table.add_row(
                str(action.get("action", "")),
                str(action.get("responsible_role", ""))
            )
        console.print(actions_table)

        # Display Predictive Risk Analysis
        pred_analysis = data.get('predictive_risk_analysis', [])
        if pred_analysis:
            pred_text = "\n".join([f"- {pa}" for pa in pred_analysis])
            console.print(f"\n[bold underline]🔮 Predictive Risk Analysis:[/bold underline]\n{pred_text}\n")

        # Display Preventative Actions as a Table
        prev_table = Table(title="🛡️ Preventative Actions", show_header=True, header_style="bold green")
        prev_table.add_column("Action")
        prev_table.add_column("Role")
      
        for prev in data.get("preventative_actions", []):
            prev_table.add_row(
                str(prev.get("action", "")),
                str(prev.get("responsible_role", ""))
            )
        console.print(prev_table)
        
        # Display Risk Assessment
        console.print(f"\n[bold red]⚠️ Risk Assessment:[/bold red] {data.get('risk_assessment', '')}\n")
        
        console.print(f"[bold green]✅ Prescriptive Analysis Complete for {breach_count} breach(es)![/bold green]")
        
        # Trigger Downstream Notifications — pass breach_summaries and attachments
        notification_agent.distribute_recommendations(
            data, kpi_name, 
            severity=severity, 
            row=row,
            breach_summaries=breach_summaries,
            attachments=attachments
        )
        
    except json.JSONDecodeError as e:
        console.print(f"[bold red]Failed to parse LLM response as JSON: {e}[/bold red]")
        console.print(f"Raw Output:\n{raw_response}")
