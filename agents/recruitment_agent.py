from rich.console import Console

import config
from clients import databricks_client

console = Console()

def run_agent(kpi_name, severity, year, month, primary_kpi=None, breach_list_json=None, **extra_params):
    """
    Triggered by the Master Orchestrator for Recruitment-related KPI breaches.
    Triggers the Databricks ML notebook for Recruitment RCA.
    
    Args:
        breach_list_json: Optional JSON string of batch breach list, e.g.
            '[{"KPI_ID": "OFB100002", "Severity": "WARNING"}, ...]'
            When provided, Databricks processes all breaches in a single run.
    """
    console.print(f"[bold yellow][Recruitment Agent][/bold yellow] Initiating Root Cause Analysis for breached KPI: [bold red]{kpi_name}[/bold red] (Severity: {severity})")
    
    try:
        # Base parameters
        params = {
            "KPI_NAME": kpi_name, 
            "SEVERITY": str(severity),
            "YEAR": str(year),
            "MONTH": str(month),
            "PRIMARY_BREACH_KPI": primary_kpi if primary_kpi else kpi_name
        }
        
        # Pass the batch breach list to Databricks if provided (matching BREACH_PAYLOAD widget)
        if breach_list_json:
            params["BREACH_PAYLOAD"] = breach_list_json
            params["BREACH_LIST"] = breach_list_json
        
        # Merge extra parameters (excluding keys that map to standard ones)
        for k, v in extra_params.items():
            k_upper = k.upper()
            if k_upper not in params and k_upper not in ("PRIMARY_KPI", "PRIMARY_BREACH_KPI", "BREACH_LIST", "BREACH_PAYLOAD"):
                params[k_upper] = str(v)

        # Submit the run to Databricks
        result = databricks_client.submit_run(
            notebook_path=config.RECRUITMENT_NOTEBOOK_PATH,
            parameters=params,
            run_name=f"Recruitment_RCA_{kpi_name.replace(' ', '_')}"
        )
        run_id = result.get("run_id")
        console.print(f"[bold yellow][Recruitment Agent][/bold yellow] Successfully triggered Databricks Job! Run ID: [bold cyan]{run_id}[/bold cyan]")
        
        return run_id
    except Exception as e:
        console.print(f"[bold red][Recruitment Agent] Failed to trigger Databricks API: {e}[/bold red]")
        return None
