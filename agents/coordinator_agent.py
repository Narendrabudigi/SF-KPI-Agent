import time
import json
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
    
    Filters results by the 'analysis_type' column:
      - ACTUAL_BREACH rows → sent to the prescriptive LLM agent
      - PREDICTIVE_RISK rows → logged for dashboard/monitoring only
    
    Returns a dict with separated context:
        {
            "actual_breach_context": str,     # Only ACTUAL_BREACH rows (for prescriptive agent)
            "predictive_risk_context": str,   # Only PREDICTIVE_RISK rows (for logging/dashboard)
            "full_context": str               # Combined context (backward compat)
        }
    Or None if no results were fetched.
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
                        state_msg = state.get("state_message", "")
                        error_detail = ""
                        try:
                            output_data = databricks_client.get_run_output(run_id)
                            error_msg = output_data.get("error", "")
                            error_trace = output_data.get("error_trace", "")
                            if error_msg or error_trace:
                                error_detail = f"\n[bold red]Databricks Error Details:[/bold red]\n{error_msg}\n{error_trace}"
                        except Exception as out_err:
                            error_detail = f" (Could not fetch notebook output: {out_err})"
                            
                        runtime_state.log_system_event(
                            f"[bold red][RCA Coordinator] Databricks Run {run_id} ({run_info['kpi_name']}) Failed![/bold red]\n"
                            f"Result State: [yellow]{result_state}[/yellow] | Message: {state_msg}{error_detail}\n"
                            f"[yellow]Attempting Databricks SQL Warehouse table fetch for {run_info['kpi_name']} ({run_info['table_name']}) as fallback...[/yellow]"
                        )
                        completed_runs.append(run_info)
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
    # Separate context blocks for ACTUAL_BREACH vs PREDICTIVE_RISK
    actual_breach_blocks = []
    predictive_risk_blocks = []
    all_blocks = []
    critical_predictive_records = []
    
    header = f"Primary Incident: {primary_kpi_name} (Severity: {severity}, Period: {year}-{month})\n"
    actual_breach_blocks.append(header)
    predictive_risk_blocks.append(header)
    all_blocks.append(header)
    
    for run_info in completed_runs:
        table_name = run_info["table_name"]
        kpi_name = run_info["kpi_name"]
        is_primary = run_info["is_primary"]
        
        runtime_state.log_system_event(f"[bold cyan][RCA Coordinator][/bold cyan] Fetching results for {kpi_name} from [bold]{table_name}[/bold]...")
        try:
            df = databricks_sql_client.fetch_results(table_name)
            # Filter the dataframe to only include rows related to the primary incident (case-insensitive column search)
            if not df.empty:
                df = df.replace(["nan", "NaN", "None", "null"], "")
                primary_col = next((c for c in df.columns if c.upper() == 'PRIMARY_BREACH_KPI'), None)
                if primary_col:
                    df = df[df[primary_col] == primary_kpi_name]
                
            if not df.empty:
                role = "PRIMARY BREACH ROOT CAUSES" if is_primary else f"CASCADING IMPACT ON {kpi_name}"
                
                # --- Filter by analysis_type column (e.g. ACTUAL_BREACH vs PREDICTIVE_RISK) ---
                analysis_col = next((c for c in df.columns if c.upper() in ('ANALYSIS_TYPE', 'ACTIVITIES_TYPE')), None)
                
                if analysis_col:
                    actual_df = df[df[analysis_col].astype(str).str.upper() == 'ACTUAL_BREACH']
                    predictive_df = df[df[analysis_col].astype(str).str.upper() == 'PREDICTIVE_RISK']
                    
                    runtime_state.log_system_event(
                        f"[bold cyan][RCA Coordinator][/bold cyan] Filtered by {analysis_col}: "
                        f"[green]{len(actual_df)} ACTUAL_BREACH[/green] | "
                        f"[yellow]{len(predictive_df)} PREDICTIVE_RISK[/yellow] rows"
                    )
                    
                    if not actual_df.empty:
                        actual_breach_blocks.append(f"--- {role} (ACTUAL BREACHES) ---\n{actual_df.to_string()}\n")
                    
                    if not predictive_df.empty:
                        predictive_risk_blocks.append(f"--- {role} (PREDICTIVE RISKS) ---\n{predictive_df.to_string()}\n")
                        # Extract CRITICAL predicted risk rows for Excel generation
                        sev_col = next((c for c in predictive_df.columns if c.upper() in ('SEVERITY', 'BREACH_SEVERITY')), None)
                        if sev_col:
                            crit_pred = predictive_df[predictive_df[sev_col].astype(str).str.upper() == 'CRITICAL']
                        else:
                            crit_pred = predictive_df
                            
                        if not crit_pred.empty:
                            records = json.loads(crit_pred.to_json(orient="records", date_format="iso"))
                            critical_predictive_records.extend(records)
                            runtime_state.log_system_event(
                                f"[bold red][RCA Coordinator][/bold red] ⚠️ Extracted {len(crit_pred)} CRITICAL PREDICTIVE_RISK row(s) for Excel export"
                            )
                    
                    # Full context includes both
                    all_blocks.append(f"--- {role} ---\n{df.to_string()}\n")
                else:
                    # No analysis_type column found — treat all rows as actual breach (backward compat)
                    runtime_state.log_system_event(
                        f"[yellow][RCA Coordinator] No 'analysis_type' column found in {table_name}. Treating all {len(df)} rows as ACTUAL_BREACH.[/yellow]"
                    )
                    actual_breach_blocks.append(f"--- {role} ---\n{df.to_string()}\n")
                    all_blocks.append(f"--- {role} ---\n{df.to_string()}\n")
            else:
                runtime_state.log_system_event(f"[bold yellow][RCA Coordinator][/bold yellow] Warning: No matching rows found in {table_name} for {primary_kpi_name}.")
                
        except Exception as sql_e:
            runtime_state.log_system_event(f"[bold red][RCA Coordinator] SQL Fetch Error for {table_name}:[/bold red] {sql_e}")
    
    # Build separated context strings
    has_actual = len(actual_breach_blocks) > 1  # More than just the header
    has_predictive = len(predictive_risk_blocks) > 1
    has_any = len(all_blocks) > 1
    
    if has_any:
        result = {
            "actual_breach_context": "\n".join(actual_breach_blocks) if has_actual else None,
            "predictive_risk_context": "\n".join(predictive_risk_blocks) if has_predictive else None,
            "critical_predictive_risks": critical_predictive_records,
            "full_context": "\n".join(all_blocks)
        }
        
        runtime_state.console.print(Panel(
            result["full_context"], 
            title="🧠 ML Context Built for LLM (Full)", 
            border_style="cyan"
        ))
        
        if has_actual:
            runtime_state.log_system_event(
                f"[bold green][RCA Coordinator][/bold green] ✅ ACTUAL_BREACH context ready for prescriptive agent ({len(actual_breach_blocks) - 1} block(s))"
            )
        if has_predictive:
            runtime_state.log_system_event(
                f"[bold yellow][RCA Coordinator][/bold yellow] 📊 PREDICTIVE_RISK context logged ({len(predictive_risk_blocks) - 1} block(s)) — NOT sent to prescriptive agent"
            )
        
        return result
    else:
        return None
