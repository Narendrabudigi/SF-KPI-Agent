import json
import time
import datetime
import threading
from rich.panel import Panel

# Import config and client from the parent root directory
import config
from clients import odata_client
import runtime_state
from clients.mcp_client import mcp_client
import breach_tracker

# Relative imports for other agents in the same folder
from agents import recruitment_agent
from agents import onboarding_agent
from agents import offboarding_agent
from agents import employee_central_agent
from agents import employee_payment_agent
from agents import coordinator_agent
from agents import prescriptive_agent

# Threading locks to serialize writes to the same Databricks tables per domain
DOMAIN_LOCKS = {
    "recruitment": threading.Lock(),
    "onboarding": threading.Lock(),
    "offboarding": threading.Lock(),
    "employee_central": threading.Lock(),
    "employee_payment": threading.Lock()
}

def get_domain_key(kpi_name):
    normalized = breach_tracker.normalize_kpi_name(kpi_name)
    if normalized == "Time-to-Hire":
        return "recruitment"
    elif normalized == "Onboarding Completion Time":
        return "onboarding"
    elif normalized == "Offboarding Completion Time":
        return "offboarding"
    elif normalized == "Employee Attrition Rate":
        return "employee_central"
    elif normalized == "Payroll Error Rate":
        return "employee_payment"
    return None

def resolve_agent_info(kpi_name):
    normalized = breach_tracker.normalize_kpi_name(kpi_name)
    if normalized == "Time-to-Hire":
        return recruitment_agent, config.RECRUITMENT_RESULTS_TABLE
    elif normalized == "Onboarding Completion Time":
        return onboarding_agent, config.ONBOARDING_RESULTS_TABLE
    elif normalized == "Offboarding Completion Time":
        return offboarding_agent, config.OFFBOARDING_RESULTS_TABLE
    elif normalized == "Employee Attrition Rate":
        return employee_central_agent, config.EMPLOYEE_CENTRAL_RESULTS_TABLE
    elif normalized == "Payroll Error Rate":
        return employee_payment_agent, config.EMPLOYEE_PAYMENT_RESULTS_TABLE
    return None, None


def handle_rca_workflow(primary_kpi_name, severity, year, month, **extra_params):
    """
    Two-Stage Sequential Workflow for Incident Cascading.
    Stage 1: Trigger primary notebook (Main Breach) and wait.
    Stage 2: Trigger dependency notebooks (Dependency Breach) and wait.
    Passes combined R (result) and D (dependency) tables to the LLM.
    """
    runtime_state.log_system_event(f"[bold red]!! BREACH DETECTED !![/bold red] Processing incident: {primary_kpi_name} (Severity: {severity}, Period: {year}-{month})")
    
    domain_key = get_domain_key(primary_kpi_name)
    if domain_key:
        runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "running"
        runtime_state.SYSTEM_STATE["primary_agent"] = domain_key
    
    runtime_state.SYSTEM_STATE["status"] = f"analyzing_{domain_key}" if domain_key else "running_stage_1"
    breach_key = f"{primary_kpi_name} ({year}-{month})"
    if breach_key not in runtime_state.SYSTEM_STATE["current_breaches"]:
        runtime_state.SYSTEM_STATE["current_breaches"].append(breach_key)
    
    matrix_entry = config.INTERDEPENDENCY_MATRIX.get(primary_kpi_name, {})
    affects = matrix_entry.get("affects", [])
    correlated_with = matrix_entry.get("correlated_with", [])
    
    # -------------------------------------------------------------------------
    # STAGE 1: Main Breach (MB)
    # -------------------------------------------------------------------------
    runtime_state.log_system_event(f"[bold cyan]STAGE 1 (Main Breach):[/bold cyan] Triggering Primary Databricks Notebook for {primary_kpi_name}...")
    
    mb_run_info_list = []
    agent_module, table_name = resolve_agent_info(primary_kpi_name)
    mb_context = None
    
    if agent_module:
        # Acquire primary domain lock for the duration of the Stage 1 Databricks job run
        primary_lock = DOMAIN_LOCKS.get(domain_key)
        if primary_lock:
            runtime_state.log_system_event(f"[bold yellow][Orchestrator][/bold yellow] Acquiring primary lock for domain: [cyan]{domain_key}[/cyan]...")
            primary_lock.acquire()
            
        run_id = None
        try:
            # Build incident_id for MCP memory keying
            incident_id = f"{primary_kpi_name.replace(' ', '_')}_{year}_{month}"

            # Store the raw OData row in MCP memory so prescriptive agent can enrich LLM prompt
            if extra_params:
                mcp_client.call_tool("store_odata_row", {
                    "incident_id": incident_id,
                    "row_data": json.dumps(extra_params)
                })

            kpi_id = (
                extra_params.get("KPI_ID") or
                extra_params.get("KPI_Id") or
                extra_params.get("kpi_id") or
                extra_params.get("Kpi_Id") or
                extra_params.get("KPI_ID_Col") or
                extra_params.get("OFFBOARDING_PROCESS_ID") or
                extra_params.get("ONBOARDING_PROCESS_ID") or
                extra_params.get("JOB_REQ_ID")
            )
            extra_params_json = json.dumps(extra_params) if extra_params else None

            run_id_str = mcp_client.call_tool("execute_domain_agent", {
                "domain": domain_key,
                "kpi_name": primary_kpi_name,
                "severity": severity,
                "year": str(year),
                "month": str(month),
                "primary_kpi": primary_kpi_name,
                "kpi_id": str(kpi_id) if kpi_id else None,
                "extra_params_json": extra_params_json
            })
            run_id = int(run_id_str) if (isinstance(run_id_str, str) and run_id_str.isdigit()) else run_id_str
            if run_id:
                mb_run_info_list.append({
                    "run_id": run_id,
                    "table_name": table_name, # R table
                    "kpi_name": primary_kpi_name,
                    "is_primary": True
                })
                # Add to active runs state
                runtime_state.SYSTEM_STATE["active_runs"].append({
                    "run_id": run_id,
                    "kpi_name": primary_kpi_name,
                    "primary_kpi": primary_kpi_name,
                    "notebook_path": config.RECRUITMENT_NOTEBOOK_PATH if "recruitment" in agent_module.__name__ else (
                        config.ONBOARDING_NOTEBOOK_PATH if "onboarding" in agent_module.__name__ else (
                            config.OFFBOARDING_NOTEBOOK_PATH if "offboarding" in agent_module.__name__ else (
                                config.EMPLOYEE_CENTRAL_NOTEBOOK_PATH if "central" in agent_module.__name__ else (
                                    config.EMPLOYEE_PAYMENT_NOTEBOOK_PATH if "payment" in agent_module.__name__ else "Unknown"
                                )
                            )
                        )
                    ),
                    "stage": "Stage 1 (Primary Analysis)"
                })
                
                runtime_state.log_system_event(f"[bold cyan]STAGE 1:[/bold cyan] Waiting for Main Breach job to complete...")
                mb_context = mcp_client.call_tool("wait_and_fetch_results_batch", {
                    "run_info_list_json": json.dumps(mb_run_info_list),
                    "primary_kpi_name": primary_kpi_name,
                    "severity": severity,
                    "year": str(year),
                    "month": str(month)
                })
        finally:
            if run_id:
                # Remove from active runs
                runtime_state.SYSTEM_STATE["active_runs"] = [
                    r for r in runtime_state.SYSTEM_STATE["active_runs"] if r["run_id"] != run_id
                ]
            if primary_lock:
                runtime_state.log_system_event(f"[bold yellow][Orchestrator][/bold yellow] Releasing primary lock for domain: [cyan]{domain_key}[/cyan]")
                primary_lock.release()
    else:
        runtime_state.log_system_event(f"[bold red]Failed to find primary agent mapped for {primary_kpi_name}[/bold red]")
        if domain_key:
            runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "failed"
        return
        
    if not mb_run_info_list or not mb_context:
        runtime_state.log_system_event("[red]Main Breach execution failed or timed out. Aborting dependency cascade.[/red]")
        if domain_key:
            runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "failed"
        return
        
    # -------------------------------------------------------------------------

    # STAGE 2: Dependency Breach (DB)
    # -------------------------------------------------------------------------
    db_context = ""
    if affects:
        runtime_state.log_system_event(f"[bold cyan]STAGE 2 (Dependency):[/bold cyan] Triggering {len(affects)} Dependency Notebooks simultaneously...")
        
        dependency_contexts = []
        dependency_contexts_lock = threading.Lock()
        
        def trigger_dependency_agent(target_kpi):
            dep_domain = get_domain_key(target_kpi)
            d_lock = DOMAIN_LOCKS.get(dep_domain)
            if d_lock:
                runtime_state.log_system_event(f"[bold yellow][Orchestrator][/bold yellow] Acquiring dependency lock for domain: [cyan]{dep_domain}[/cyan]...")
                d_lock.acquire()
            
            run_info = None
            try:
                agent_mod, _ = resolve_agent_info(target_kpi)
                if agent_mod:
                    run_id_str = mcp_client.call_tool("execute_domain_agent", {
                        "domain": dep_domain,
                        "kpi_name": target_kpi,
                        "severity": "CASCADING_ANALYSIS",
                        "year": str(year),
                        "month": str(month),
                        "primary_kpi": primary_kpi_name,
                        "extra_params_json": extra_params_json
                    })
                    run_id = int(run_id_str) if (isinstance(run_id_str, str) and run_id_str.isdigit()) else run_id_str
                    if run_id:
                        # Dynamically determine the D table name based on agent domain
                        d_table = "workspace.default.recruitment_dependency_results"
                        if "onboarding" in agent_mod.__name__:
                            d_table = "workspace.default.onboarding_dependency_results"
                        elif "offboarding" in agent_mod.__name__:
                            d_table = "workspace.default.offboarding_dependency_results"
                        elif "employee_central" in agent_mod.__name__:
                            d_table = "workspace.default.employee_central_dependency_results"
                        elif "employee_payment" in agent_mod.__name__:
                            d_table = "workspace.default.employee_payment_dependency_results"
                            
                        run_info = {
                            "run_id": run_id,
                            "table_name": d_table, # D table
                            "kpi_name": target_kpi,
                            "is_primary": False
                        }
                        
                        active_run_entry = {
                            "run_id": run_id,
                            "kpi_name": target_kpi,
                            "primary_kpi": primary_kpi_name,
                            "notebook_path": config.RECRUITMENT_NOTEBOOK_PATH if "recruitment" in agent_mod.__name__ else (
                                config.ONBOARDING_NOTEBOOK_PATH if "onboarding" in agent_mod.__name__ else (
                                    config.OFFBOARDING_NOTEBOOK_PATH if "offboarding" in agent_mod.__name__ else (
                                        config.EMPLOYEE_CENTRAL_NOTEBOOK_PATH if "central" in agent_mod.__name__ else (
                                            config.EMPLOYEE_PAYMENT_NOTEBOOK_PATH if "payment" in agent_mod.__name__ else "Unknown"
                                        )
                                    )
                                )
                            ),
                            "stage": "Stage 2 (Dependency Analysis)"
                        }
                        runtime_state.SYSTEM_STATE["active_runs"].append(active_run_entry)
                        
                        # Wait and fetch results for this dependency run
                        res_context = mcp_client.call_tool("wait_and_fetch_results_batch", {
                            "run_info_list_json": json.dumps([run_info]),
                            "primary_kpi_name": primary_kpi_name,
                            "severity": severity,
                            "year": str(year),
                            "month": str(month)
                        })
                        if res_context:
                            with dependency_contexts_lock:
                                dependency_contexts.append(res_context)
            finally:
                if run_info:
                    runtime_state.SYSTEM_STATE["active_runs"] = [
                        r for r in runtime_state.SYSTEM_STATE["active_runs"] if r["run_id"] != run_info["run_id"]
                    ]
                if d_lock:
                    runtime_state.log_system_event(f"[bold yellow][Orchestrator][/bold yellow] Releasing dependency lock for domain: [cyan]{dep_domain}[/cyan]")
                    d_lock.release()
                    
        threads = []
        for dep_kpi in affects:
            t = threading.Thread(target=trigger_dependency_agent, args=(dep_kpi,))
            t.start()
            threads.append(t)
            
        for t in threads:
            t.join()
            
        if dependency_contexts:
            db_context = "\n\n".join(dependency_contexts)
    else:
        runtime_state.log_system_event("[dim]No dependencies to trigger for this KPI.[/dim]")
        
    # -------------------------------------------------------------------------
    # STAGE 3: Combine & Prescribe
    # -------------------------------------------------------------------------
    runtime_state.SYSTEM_STATE["status"] = "prescribing"
    full_combined_context = f"{mb_context}\n\n{db_context}" if db_context else mb_context
    
    # Store incident context in MCP Memory
    incident_id = f"{primary_kpi_name.replace(' ', '_')}_{year}_{month}"
    mcp_client.call_tool("store_incident_context", {"incident_id": incident_id, "context_data": full_combined_context})
    
    mcp_client.call_tool("run_prescriptive_analysis", {
        "incident_id": incident_id,
        "kpi_name": primary_kpi_name,
        "correlated_with_json": json.dumps(correlated_with) if correlated_with else None,
        "severity": severity,
        "row_json": json.dumps(extra_params) if extra_params else None
    })
    
    if domain_key:
        if runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] != "failed":
            runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "completed"

    # Remove from active breaches
    if breach_key in runtime_state.SYSTEM_STATE["current_breaches"]:
        runtime_state.SYSTEM_STATE["current_breaches"].remove(breach_key)

def route_kpi_breach(kpi_name, severity, year, month, **extra_params):
    """Wrapper to start the incident workflow."""
    if not kpi_name:
        runtime_state.log_system_event("[bold red]Error: Received empty or invalid KPI Name to route[/bold red]")
        return
        
    kpi_name_clean = breach_tracker.normalize_kpi_name(kpi_name)
    handle_rca_workflow(kpi_name_clean, severity, year, month, **extra_params)

def check_sap_breaches():
    """
    Fetches KPI data from SAP and routes any breaches to the correct domain agent.
    Can be run once manually or in a loop.
    """
    runtime_state.SYSTEM_STATE["primary_agent"] = None
    runtime_state.SYSTEM_STATE["status"] = "fetching"
    runtime_state.SYSTEM_STATE["last_checked"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    runtime_state.log_system_event("Checking OData endpoint for active breaches...")
    
    kpi_data = odata_client.fetch_kpi_data()
    
    if kpi_data:
        breach_found = False
        current_active_ids = set()  # Track all active breach IDs for reconciliation
        
        # Sort the kpi_data so Critical breaches (Critical=1) are processed first
        sorted_kpi_data = sorted(
            kpi_data, 
            key=lambda r: int(r.get("Critical", 0)) if r.get("Critical") is not None else 0, 
            reverse=True
        )
        
        threads = []
        for row in sorted_kpi_data:
            raw_kpi = row.get("KPI_Name")
            kpi_name = breach_tracker.normalize_kpi_name(raw_kpi)
            
            # Check for the breach field (supporting both 'Breach' and 'KPI_Breach')
            breach_val = row.get("Breach") or row.get("KPI_Breach")
            if breach_val == "X":
                breach_found = True
                
                # Generate a unique KPI-aware breach ID and register it as active
                bid = breach_tracker.generate_breach_id(kpi_name, row)
                current_active_ids.add(bid)

                # Check cooldown — skip if we already notified for this breach recently
                in_cd, last_notified, remaining = breach_tracker.is_in_cooldown(bid)
                if in_cd:
                    runtime_state.log_system_event(
                        f"[yellow]⏳ Breach '{bid}' is in cooldown window ({remaining}m remaining). Skipping duplicate alert.[/yellow]"
                    )
                    continue
                
                # Determine Severity based on fields
                is_critical = str(row.get("Critical")) == "1" or str(row.get("Severity", "")).lower() == "critical"
                severity = "Critical" if is_critical else "Warning"
                
                year = row.get("Year") or row.get("Posting_Year")
                month = row.get("Month") or row.get("Posting_Month")
                
                # Collect and filter all non-null / non-empty parameters from the OData row
                extra_params = {}
                for k, v in row.items():
                    if v is None or v == "" or str(v).upper() in ("NULL", "NONE"):
                        continue
                    extra_params[k] = v
                
                runtime_state.log_system_event(f"🎯 Triggering Domain Agent dynamically for: [bold]{kpi_name}[/bold]")
                
                # Run each breach in parallel; safety locks inside route_kpi_breach serialize runs on the same domain
                t = threading.Thread(target=route_kpi_breach, args=(kpi_name, severity, year, month), kwargs=extra_params)
                t.start()
                threads.append(t)
                
        # Wait for all simultaneous breach threads to finish before returning.
        for t in threads:
            t.join()

        # Reconcile: purge any previously-active breaches that are no longer in OData
        purged = breach_tracker.reconcile_active_breaches(current_active_ids)
        if purged:
            runtime_state.log_system_event(f"[green]🧹 Purged {len(purged)} resolved breach(es) from active_breaches.json: {purged}[/green]")
                
        if not breach_found:
            runtime_state.log_system_event("[dim]No breaches detected in this cycle.[/dim]")
            
    runtime_state.SYSTEM_STATE["status"] = "completed"
    time.sleep(2)
    runtime_state.SYSTEM_STATE["status"] = "idle"

def reset_memory_file():
    import os
    import json
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    MEMORY_FILE = os.path.join(BASE_DIR, "mcp_memory.json")
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump({}, f, indent=4)
        runtime_state.log_system_event("MCP Memory file successfully reset at 8:50 AM.")
    except Exception as e:
        runtime_state.log_system_event(f"[bold red]Failed to reset memory file: {e}[/bold red]")

def run_orchestrator():
    """
    Master Orchestrator Loop:
    Checks OData endpoint at exactly 9:00 AM every day.
    """
    runtime_state.log_system_event("HR Master Service initialized.")
    
    # Run once immediately on startup so results are available immediately
    runtime_state.log_system_event("Starting initial boot check...")
    try:
        check_sap_breaches()
    except Exception as e:
        runtime_state.log_system_event(f"[bold red]Startup check failed: {e}[/bold red]")
        runtime_state.SYSTEM_STATE["status"] = "idle"
        
    while True:
        # Sleep until 8:50 AM first
        now = datetime.datetime.now()
        target_reset = now.replace(hour=8, minute=50, second=0, microsecond=0)
        if now >= target_reset:
            target_reset += datetime.timedelta(days=1)
            
        seconds_to_sleep_reset = (target_reset - now).total_seconds()
        hours_reset = int(seconds_to_sleep_reset // 3600)
        minutes_reset = int((seconds_to_sleep_reset % 3600) // 60)
        runtime_state.log_system_event(f"Sleeping until 8:50 AM to reset memory. Next reset scheduled at {target_reset.strftime('%Y-%m-%d %H:%M:%S')} (in {hours_reset}h {minutes_reset}m).")
        
        # Sleep until 8:50 AM
        time.sleep(seconds_to_sleep_reset)
        
        # Reset memory file
        reset_memory_file()
        
        # Sleep remaining 10 minutes until 9:00 AM
        now = datetime.datetime.now()
        target_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= target_run:
            target_run += datetime.timedelta(days=1)
            
        seconds_to_sleep_run = (target_run - now).total_seconds()
        if seconds_to_sleep_run > 0:
            time.sleep(seconds_to_sleep_run)
            
        runtime_state.log_system_event("Daily Scheduled Trigger Initiated!")
        try:
            check_sap_breaches()
        except Exception as e:
            runtime_state.log_system_event(f"[bold red]Scheduled daily check failed: {e}[/bold red]")
            runtime_state.SYSTEM_STATE["status"] = "idle"

if __name__ == "__main__":
    try:
        run_orchestrator()
    except KeyboardInterrupt:
        runtime_state.log_system_event("Orchestrator stopped by user.")
