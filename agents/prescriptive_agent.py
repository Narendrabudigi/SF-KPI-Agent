import json
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from clients import llm_client
from agents import notification_agent

console = Console()

SYSTEM_PROMPT = """You are an expert SAP SuccessFactors HR analyst specializing in Recruitment, Onboarding, Offboarding, Employee Central, and Employee Central Payroll operations. 
Your role is to analyze HR KPI breach root causes and provide actionable, prescriptive recommendations for the PRIMARY incident.

Guidelines:
1. Focus your actionable recommendations on resolving the PRIMARY BREACH.
2. If cascading impacts on other HR KPIs or historical correlations are provided, factor them into your root cause narrative, explaining the logical, departmental, or process-level impact (e.g., how high attrition in Legal & Compliance will lead to an increase in offboarding queue volume and delay offboarding completion times). Do NOT assume or make up record-level linkages between them if they are not explicitly present in the data.
3. Be specific, extremely detailed, and actionable - avoid generic advice.
4. Consider SAP SuccessFactors capabilities in your recommendations.
5. Address both immediate fixes and long-term improvements to reduce future failures and support business development.
6. Quantify expected impact where possible.
7. Consider cross-functional HR impacts.
8. Prioritize recommendations by effort vs. impact.
9. ANALYZE DETAIL DATA: Carefully inspect the detailed employee or candidate level data provided in the root table and dependency table within the root cause analysis findings. Detail exactly WHY the breach occurred, and BECAUSE OF WHOM (identifying the specific candidate IDs, employee IDs, department names, or responsible owners who caused/are experiencing the delay) ONLY IF they are explicitly present in the provided OData row or root cause details. Strictly do NOT invent, hallucinate, or assume fake employee IDs (e.g., EMP100001, EMP100002) or candidate records if they are not in the context.
10. HIGHLIGHT IMPORTANT THINGS: In your descriptions, executive summaries, root causes, and precautions, highlight critical values, specific names, employee IDs, percentages, and durations using markdown bolding (e.g., **Engineering**, **EMP100001**, **45.6%**, **4 days**). Only highlight real values present in the input.
11. DETAILED EXPLANATION: Write detailed, context-rich analysis and recommendations rather than brief summaries. Provide deep business insights that help in process optimization.
12. NO RECORD HALLUCINATIONS: Do not populate "affected_records" or invent specific delayed records for dependent or correlated KPIs that are department-level or do not have matching record-level details in the input context. Keep "affected_records" empty or strictly limited to actual records provided in the inputs.

Format your response as valid JSON with the following structure:
{
    "executive_summary": "...",
    "root_causes": [
        "Point 1...",
        "Point 2...",
        "Point 3...",
        "Point 4...",
        "Point 5..."
    ],
    "immediate_actions": [
        {
            "action": "...",
            "responsible_role": "...",
            "expected_impact": "..."
        }
    ],
    "medium_term_actions": ["..."],
    "long_term_actions": ["..."],
    "expected_impact": "...",
    "risk_assessment": "...",
    "detailed_breach_analysis": {
        "breach_reason": "Detailed explanation of why the breach happened based on the root and dependency tables.",
        "responsible_parties": "Explanation of because of whom (specific candidates, employees, departments, or system owners) the breach occurred.",
        "affected_records": [
            {
                "record_id": "Employee/Candidate ID or Name (leave empty if no specific record-level detail exists in input context)",
                "reason_for_delay": "Specific reason for delay or failure associated with this record",
                "responsible_person": "Owner/Manager/Role responsible for this record's progress"
            }
        ]
    },
    "precautions": [
        "Precaution 1...",
        "Precaution 2..."
    ]
}
"""

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

def run_prescriptive_analysis(incident_id: str, kpi_name: str, correlated_with: list = None, severity: str = "Critical", row: dict = None):
    """
    Takes the incident ID, retrieves the ML findings context from the MCP memory,
    and passes them to the LLM to generate prescriptive business recommendations.
    
    Args:
        incident_id: Unique breach incident key used to retrieve context from MCP memory
        kpi_name: The primary breached KPI name
        correlated_with: List of historically correlated KPI names
        severity: Breach severity ('Critical' or 'Warning') — forwarded to notification routing
        row: The original OData SAP row dict — stored in MCP memory and used to identify specific records
    """
    from clients.mcp_client import mcp_client
    ml_context = mcp_client.call_tool("get_incident_context", {"incident_id": incident_id})
    console.print(Panel(f"[bold magenta]Starting Prescriptive Analysis for Primary Incident: {kpi_name}...[/bold magenta]", border_style="magenta"))
    
    # Retrieve the original OData SAP row (employee/candidate level detail from SuccessFactors)
    # This enriches the LLM prompt with actual record-level context — who caused the breach, which dept, etc.
    odata_row_str = mcp_client.call_tool("get_odata_row", {"incident_id": incident_id})
    odata_context = ""
    if odata_row_str and "No OData row found" not in odata_row_str:
        try:
            import json as _json
            odata_row = _json.loads(odata_row_str)
            odata_context = (
                f"\n\n--- ORIGINAL SAP OData BREACH ROW ---\n"
                f"(Employee/Candidate Level Detail from SuccessFactors — use this to identify WHO caused the breach)\n"
                f"{_json.dumps(odata_row, indent=2)}\n"
            )
            console.print(f"[dim]📎 OData row context injected into LLM prompt ({len(odata_row)} fields)[/dim]")
        except Exception:
            odata_context = f"\n\nSAP OData Row (raw): {odata_row_str}\n"

    correlation_text = ""
    if correlated_with:
        correlation_text = f"\nFYI: This breach is historically correlated with the following KPIs: {', '.join(correlated_with)}. Keep this in mind during your analysis.\n"
        
    # Construct User Prompt — includes Databricks ML results + OData row detail + correlation context
    user_prompt = f"""Based on the following ML Root Cause Findings, provide your prescriptive recommendations to solve the primary breach of {kpi_name}:
{correlation_text}
{odata_context}
{ml_context}
"""
    
    # Call the centralized LLM client (which handles fallbacks internally)
    raw_response = llm_client.generate_recommendation(SYSTEM_PROMPT, user_prompt)
    
    if not raw_response:
        console.print("[bold red]Failed to generate recommendations from any LLM provider.[/bold red]")
        return
        
    # Clean and parse the response
    try:
        cleaned_response = _clean_json_response(raw_response)
        data = json.loads(cleaned_response)
        
        # Display Executive Summary
        console.print(Panel(data.get("executive_summary", ""), title="📊 Executive Summary", border_style="green"))
        
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
        
        # Display Risk Assessment
        console.print(f"\n[bold red]⚠️ Risk Assessment:[/bold red] {data.get('risk_assessment', '')}\n")
        
        console.print("[bold green]✅ Prescriptive Analysis Complete![/bold green]")
        
        # Trigger Downstream Notifications (pass severity and original row for breach recording)
        notification_agent.distribute_recommendations(data, kpi_name, severity=severity, row=row)
        
    except json.JSONDecodeError as e:
        console.print(f"[bold red]Failed to parse LLM response as JSON: {e}[/bold red]")
        console.print(f"Raw Output:\n{raw_response}")
