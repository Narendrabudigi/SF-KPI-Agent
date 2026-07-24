import time
import pandas as pd
from rich.console import Console
from rich.panel import Panel

import config
from clients import databricks_client
from clients import databricks_sql_client

import runtime_state

def wait_and_fetch_results_batch(run_info_list: list, primary_kpi_name: str, severity: str, year: str, month: str):
    """
    Waits for MULTIPLE Databricks Notebook runs to complete, then fetches all their output tables via SQL.
    Returns a combined Problem Context for the Prescriptive LLM Agent.
    """
    runtime_state.log_system_event(f"[bold cyan][RCA Coordinator][/bold cyan] Monitoring {len(run_info_list)} Databricks Runs simultaneously...")
    
    start_time = time.time()
    pending_runs = list(run_info_list)
    completed_runs = []
    
    while pending_runs:
        for run_info in list(pending_runs):
            run_id = run_info["run_id"]
            try:
                run_data = databricks_client.get_run_status(run_id)
                state = run_data.get("state", {})
                lifecycle = state.get("life_cycle_state", "UNKNOWN")
                
                if lifecycle in ["TERMINATED", "SKIPPED", "INTERNAL_ERROR"]:
                    result_state = state.get("result_state", "")
                    
                    if result_state == "SUCCESS":
                        runtime_state.log_system_event(f"[bold green][RCA Coordinator][/bold green] Run {run_id} ({run_info['kpi_name']}) completed!")
                        completed_runs.append(run_info)
                        pending_runs.remove(run_info)
                    else:
                        runtime_state.log_system_event(f"[bold red][RCA Coordinator] Databricks Run {run_id} ({run_info['kpi_name']}) Failed![/bold red] Result State: {result_state}")
                        pending_runs.remove(run_info)
                        
            except Exception as e:
                runtime_state.log_system_event(f"[bold red][RCA Coordinator] API Error on Run {run_id}:[/bold red] {e}")
                
        if pending_runs:
            elapsed = time.time() - start_time
            if elapsed > config.MAX_WAIT_SECONDS:
                runtime_state.log_system_event(f"[bold red][RCA Coordinator] Timeout waiting for runs.[/bold red]")
                break
            time.sleep(config.DATABRICKS_POLL_INTERVAL)
            
    # Now fetch SQL tables for all completed runs
    context_blocks = []
    context_blocks.append(f"Primary Incident: {primary_kpi_name} (Severity: {severity}, Period: {year}-{month})\n")
    
    for run_info in completed_runs:
        table_name = run_info["table_name"]
        kpi_name = run_info["kpi_name"]
        is_primary = run_info["is_primary"]
        
        runtime_state.log_system_event(f"[bold cyan][RCA Coordinator][/bold cyan] Fetching results for {kpi_name} from [bold]{table_name}[/bold]...")
        try:
            df = databricks_sql_client.fetch_results(table_name)
            # Filter the dataframe to only include rows related to the primary incident (case-insensitive column search)
            if not df.empty:
                primary_col = next((c for c in df.columns if c.upper() == 'PRIMARY_BREACH_KPI'), None)
                if primary_col:
                    df = df[df[primary_col] == primary_kpi_name]
                
            if not df.empty:
                role = "PRIMARY BREACH ROOT CAUSES" if is_primary else f"CASCADING IMPACT ON {kpi_name}"
                context_blocks.append(f"--- {role} ---\n{df.to_string()}\n")
            else:
                runtime_state.log_system_event(f"[bold yellow][RCA Coordinator][/bold yellow] Warning: No matching rows found in {table_name} for {primary_kpi_name}.")
                
        except Exception as sql_e:
            runtime_state.log_system_event(f"[bold red][RCA Coordinator] SQL Fetch Error for {table_name}:[/bold red] {sql_e}")
            
    if len(context_blocks) > 1:
        full_context = "\n".join(context_blocks)
        runtime_state.console.print(Panel(full_context, title="🧠 ML Context Built for LLM", border_style="cyan"))
        return full_context
    else:
        return None
