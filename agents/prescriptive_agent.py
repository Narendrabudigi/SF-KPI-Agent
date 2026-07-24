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
3. Be specific and actionable - avoid generic advice
4. Consider SAP SuccessFactors capabilities in your recommendations
5. Address both immediate fixes and long-term improvements
6. Quantify expected impact where possible
7. Consider cross-functional HR impacts
8. Prioritize recommendations by effort vs. impact

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
            "sap_transaction": "...",
            "expected_impact": "...",
            "effort": "low/medium/high"
        }
    ],
    "medium_term_actions": ["..."],
    "long_term_actions": ["..."],
    "expected_impact": "...",
    "risk_assessment": "..."
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

def run_prescriptive_analysis(ml_context: str, kpi_name: str, correlated_with: list = None):
    """
    Takes the ML findings from Databricks and passes them to the LLM
    to generate prescriptive business recommendations.
    """
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
        actions_table.add_column("SAP T-Code")
        actions_table.add_column("Effort")
        
        for action in data.get("immediate_actions", []):
            actions_table.add_row(
                str(action.get("action", "")),
                str(action.get("responsible_role", "")),
                str(action.get("sap_transaction", "")),
                str(action.get("effort", ""))
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
