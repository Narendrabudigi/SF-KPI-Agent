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
2. If cascading impacts on other HR KPIs or historical correlations are provided, factor them into your root cause narrative, but your main goal is fixing the primary KPI.
3. Be specific, extremely detailed, and actionable - avoid generic advice.
4. Consider SAP SuccessFactors capabilities in your recommendations.
5. Address both immediate fixes and long-term improvements to reduce future failures and support business development.
6. Quantify expected impact where possible.
7. Consider cross-functional HR impacts.
8. Prioritize recommendations by effort vs. impact.
9. ANALYZE DETAIL DATA: Carefully inspect the detailed employee or candidate level data provided in the root table and dependency table within the root cause analysis findings. Detail exactly WHY the breach occurred, BECAUSE OF WHOM (identifying the specific candidate IDs, employee IDs, department names, or responsible owners who caused/are experiencing the delay), the PRECAUTIONS to prevent such delays, and FUTURE SOLUTIONS to automate or streamline this process.
10. HIGHLIGHT IMPORTANT THINGS: In your descriptions, executive summaries, root causes, precautions, and future solutions, highlight critical values, specific names, employee IDs, percentages, and durations using markdown bolding (e.g., **Engineering**, **EMP100001**, **45.6%**, **4 days**).
11. DETAILED EXPLANATION: Write detailed, context-rich analysis and recommendations rather than brief summaries. Provide deep business insights that help in process optimization.

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
                "record_id": "Employee/Candidate ID or Name",
                "reason_for_delay": "Specific reason for delay or failure associated with this record",
                "responsible_person": "Owner/Manager/Role responsible for this record's progress"
            }
        ]
    },
    "precautions": [
        "Precaution 1...",
        "Precaution 2..."
    ],
    "future_solutions": [
        "Future solution 1...",
        "Future solution 2..."
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

def run_prescriptive_analysis(incident_id: str, kpi_name: str, correlated_with: list = None):
    """
    Takes the incident ID, retrieves the ML findings context from the MCP memory,
    and passes them to the LLM to generate prescriptive business recommendations.
    """
    from clients.mcp_client import mcp_client
    ml_context = mcp_client.call_tool("get_incident_context", {"incident_id": incident_id})
    console.print(Panel(f"[bold magenta]Starting Prescriptive Analysis for Primary Incident: {kpi_name}...[/bold magenta]", border_style="magenta"))
    
    correlation_text = ""
    if correlated_with:
        correlation_text = f"\nFYI: This breach is historically correlated with the following KPIs: {', '.join(correlated_with)}. Keep this in mind during your analysis.\n"
        
    # Construct User Prompt
    user_prompt = f"""Based on the following ML Root Cause Findings, provide your prescriptive recommendations to solve the primary breach of {kpi_name}:
{correlation_text}
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
        
        # Trigger Downstream Notifications
        notification_agent.distribute_recommendations(data, kpi_name)
        
    except json.JSONDecodeError as e:
        console.print(f"[bold red]Failed to parse LLM response as JSON: {e}[/bold red]")
        console.print(f"Raw Output:\n{raw_response}")
