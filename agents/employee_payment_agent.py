from rich.console import Console

import config
from clients import databricks_client

console = Console()

def run_agent(kpi_name, severity, year, month, primary_kpi=None, **extra_params):
    """
    Triggered by the Master Orchestrator for Employee Payment-related KPI breaches.
    Triggers the Databricks ML notebook for Employee Central Payroll RCA.
    """
    console.print(f"[bold cyan][Employee Payment Agent][/bold cyan] Initiating Root Cause Analysis for breached KPI: [bold red]{kpi_name}[/bold red] (Severity: {severity})")
    
    try:
        # Base parameters
        params = {
            "KPI_NAME": kpi_name, 
            "SEVERITY": str(severity),
            "YEAR": str(year),
            "MONTH": str(month),
            "PRIMARY_BREACH_KPI": primary_kpi if primary_kpi else kpi_name
        }
        
        # Merge extra parameters (excluding keys that map to standard ones)
        for k, v in extra_params.items():
            k_upper = k.upper()
            if k_upper not in params and k_upper not in ("PRIMARY_KPI", "PRIMARY_BREACH_KPI"):
                params[k_upper] = str(v)

        # Submit the run to Databricks
        result = databricks_client.submit_run(
            notebook_path=config.EMPLOYEE_PAYMENT_NOTEBOOK_PATH,
            parameters=params,
            run_name=f"EmployeePayment_RCA_{kpi_name.replace(' ', '_')}"
        )
        run_id = result.get("run_id")
        console.print(f"[bold cyan][Employee Payment Agent][/bold cyan] Successfully triggered Databricks Job! Run ID: [bold yellow]{run_id}[/bold yellow]")
        
        return run_id
    except Exception as e:
        console.print(f"[bold red][Employee Payment Agent] Failed to trigger Databricks API: {e}[/bold red]")
        return None
