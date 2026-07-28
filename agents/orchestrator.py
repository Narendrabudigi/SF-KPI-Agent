import time
import datetime
import threading
from rich.panel import Panel

# Import config and client from the parent root directory
import config
from clients import odata_client
import runtime_state

# Relative imports for other agents in the same folder
from agents import recruitment_agent
from agents import onboarding_offboarding_agent
from agents import employee_central_agent
from agents import employee_payment_agent
from agents import coordinator_agent
from agents import prescriptive_agent

# Threading locks to serialize writes to the same Databricks tables per domain
DOMAIN_LOCKS = {
    "recruitment": threading.Lock(),
    "onboarding_offboarding": threading.Lock(),
    "employee_central": threading.Lock(),
    "employee_payment": threading.Lock()
}

def get_domain_key(kpi_name):
    kpi_lower = kpi_name.lower()
    if "time-to-hire" in kpi_lower or "time to hire" in kpi_lower or kpi_lower == "tth":
        return "recruitment"
    elif "onboarding" in kpi_lower or "offboarding" in kpi_lower or kpi_lower == "oct" or kpi_lower == "oft":
        return "onboarding_offboarding"
    elif "attrition" in kpi_lower or kpi_lower == "ear":
        return "employee_central"
    elif "payroll" in kpi_lower or kpi_lower == "per":
        return "employee_payment"
    return None

def resolve_agent_info(kpi_name):
    kpi_lower = kpi_name.lower()
    if "time-to-hire" in kpi_lower or "time to hire" in kpi_lower or kpi_lower == "tth":
        return recruitment_agent, config.RECRUITMENT_RESULTS_TABLE
    elif "onboarding" in kpi_lower or kpi_lower == "oct":
        return onboarding_offboarding_agent, config.ONBOARDING_OFFBOARDING_RESULTS_TABLE
    elif "offboarding" in kpi_lower or kpi_lower == "oft":
        return onboarding_offboarding_agent, config.ONBOARDING_OFFBOARDING_RESULTS_TABLE
    elif "attrition" in kpi_lower or kpi_lower == "ear":
        return employee_central_agent, config.EMPLOYEE_CENTRAL_RESULTS_TABLE
    elif "payroll" in kpi_lower or kpi_lower == "per":
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
    
    if agent_module:
        run_id = agent_module.run_agent(primary_kpi_name, severity, year, month, primary_kpi=primary_kpi_name, **extra_params)
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
                "notebook_path": config.RECRUITMENT_NOTEBOOK_PATH if "recruitment" in agent_module.__name__ else (
                    config.ONBOARDING_OFFBOARDING_NOTEBOOK_PATH if "onboarding" in agent_module.__name__ else (
                        config.EMPLOYEE_CENTRAL_NOTEBOOK_PATH if "central" in agent_module.__name__ else (
                            config.EMPLOYEE_PAYMENT_NOTEBOOK_PATH if "payment" in agent_module.__name__ else "Unknown"
                        )
                    )
                ),
                "stage": "Stage 1 (Primary Analysis)"
            })
    else:
        runtime_state.log_system_event(f"[bold red]Failed to find primary agent mapped for {primary_kpi_name}[/bold red]")
        if domain_key:
            runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "failed"
        return
        
    if not mb_run_info_list:
        runtime_state.log_system_event("[red]Failed to trigger primary Databricks job.[/red]")
        if domain_key:
            runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "failed"
        return
        
    runtime_state.log_system_event(f"[bold cyan]STAGE 1:[/bold cyan] Waiting for Main Breach job to complete...")
    mb_context = coordinator_agent.wait_and_fetch_results_batch(
        mb_run_info_list, 
        primary_kpi_name, 
        severity, 
        year, 
        month
    )
    
    # Remove from active runs
    runtime_state.SYSTEM_STATE["active_runs"] = [
        r for r in runtime_state.SYSTEM_STATE["active_runs"] if r["run_id"] != run_id
    ]
    
    if not mb_context:
        runtime_state.log_system_event("[red]Main Breach execution failed. Aborting dependency cascade.[/red]")
        if domain_key:
            runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "failed"
        return
        
    if domain_key:
        runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "completed"
        
    # -------------------------------------------------------------------------
    # STAGE 2: Dependency Breach (DB)
    # -------------------------------------------------------------------------
    db_run_info_list = []
    if affects:
        runtime_state.log_system_event(f"[bold cyan]STAGE 2 (Dependency):[/bold cyan] Triggering {len(affects)} Dependency Notebooks simultaneously...")
        
        # Acquire locks for each dependency domain before running
        dep_locks_acquired = []
        for dep_kpi in affects:
            dep_domain = get_domain_key(dep_kpi)
            d_lock = DOMAIN_LOCKS.get(dep_domain)
            if d_lock:
                runtime_state.log_system_event(f"[bold yellow][Orchestrator][/bold yellow] Acquiring dependency lock for domain: [cyan]{dep_domain}[/cyan]...")
                d_lock.acquire()
                dep_locks_acquired.append((dep_domain, d_lock))
                if dep_domain:
                    runtime_state.SYSTEM_STATE["agent_statuses"][dep_domain] = "running"
                
        try:
            def trigger_dependency_agent(target_kpi):
                agent_mod, _ = resolve_agent_info(target_kpi)
                if agent_mod:
                    run_id = agent_mod.run_agent(target_kpi, "CASCADING_ANALYSIS", year, month, primary_kpi=primary_kpi_name, **extra_params)
                    if run_id:
                        # Dynamically determine the D table name based on agent domain
                        d_table = "workspace.default.recruitment_dependency_results"
                        if "onboarding_offboarding" in agent_mod.__name__:
                            d_table = "workspace.default.onboarding_offboarding_dependency_results"
                        elif "employee_central" in agent_mod.__name__:
                            d_table = "workspace.default.employee_central_dependency_results"
                        elif "employee_payment" in agent_mod.__name__:
                            d_table = "workspace.default.employee_payment_dependency_results"
                            
                        db_run_info_list.append({
                            "run_id": run_id,
                            "table_name": d_table, # D table
                            "kpi_name": target_kpi,
                            "is_primary": False
                        })
                        runtime_state.SYSTEM_STATE["active_runs"].append({
                            "run_id": run_id,
                            "kpi_name": target_kpi,
                            "notebook_path": config.RECRUITMENT_NOTEBOOK_PATH if "recruitment" in agent_mod.__name__ else (
                                config.ONBOARDING_OFFBOARDING_NOTEBOOK_PATH if "onboarding" in agent_mod.__name__ else (
                                    config.EMPLOYEE_CENTRAL_NOTEBOOK_PATH if "central" in agent_mod.__name__ else (
                                        config.EMPLOYEE_PAYMENT_NOTEBOOK_PATH if "payment" in agent_mod.__name__ else "Unknown"
                                    )
                                )
                            ),
                            "stage": "Stage 2 (Dependency Analysis)"
                        })
                else:
                    runtime_state.log_system_event(f"[yellow]Warning: No agent mapped for dependency {target_kpi}[/yellow]")
                    
            threads = []
            for dep_kpi in affects:
                t = threading.Thread(target=trigger_dependency_agent, args=(dep_kpi,))
                t.start()
                threads.append(t)
                
            for t in threads:
                t.join()
                
            if db_run_info_list:
                runtime_state.log_system_event(f"[bold cyan]STAGE 2:[/bold cyan] Waiting for {len(db_run_info_list)} Dependency jobs to complete...")
                db_context = coordinator_agent.wait_and_fetch_results_batch(
                    db_run_info_list, 
                    primary_kpi_name, 
                    severity, 
                    year, 
                    month
                )
            else:
                db_context = ""
        finally:
            # Remove all Stage 2 runs from active runs status
            for run_info in db_run_info_list:
                runtime_state.SYSTEM_STATE["active_runs"] = [
                    r for r in runtime_state.SYSTEM_STATE["active_runs"] if r["run_id"] != run_info["run_id"]
                ]
                
            # Release dependency locks
            for dep_domain, d_lock in dep_locks_acquired:
                runtime_state.log_system_event(f"[bold yellow][Orchestrator][/bold yellow] Releasing dependency lock for domain: [cyan]{dep_domain}[/cyan]")
                if runtime_state.SYSTEM_STATE["agent_statuses"][dep_domain] == "running":
                    runtime_state.SYSTEM_STATE["agent_statuses"][dep_domain] = "completed"
                d_lock.release()
    else:
        db_context = ""
        runtime_state.log_system_event("[dim]No dependencies to trigger for this KPI.[/dim]")
        
    # -------------------------------------------------------------------------
    # STAGE 3: Combine & Prescribe
    # -------------------------------------------------------------------------
    runtime_state.SYSTEM_STATE["status"] = "prescribing"
    full_combined_context = f"{mb_context}\n\n{db_context}" if db_context else mb_context
    
    prescriptive_agent.run_prescriptive_analysis(
        full_combined_context, 
        primary_kpi_name,
        correlated_with=correlated_with
    )
    
    # Remove from active breaches
    if breach_key in runtime_state.SYSTEM_STATE["current_breaches"]:
        runtime_state.SYSTEM_STATE["current_breaches"].remove(breach_key)

def route_kpi_breach(kpi_name, severity, year, month, **extra_params):
    """Wrapper to start the incident workflow."""
    if not kpi_name:
        runtime_state.log_system_event("[bold red]Error: Received empty or invalid KPI Name to route[/bold red]")
        return
        
    # Normalize KPI Name
    kpi_name_clean = kpi_name.strip()
    kpi_lower = kpi_name_clean.lower()
    
    # Map common variations to standard matrix/config keys
    if "time to hire" in kpi_lower or "time-to-hire" in kpi_lower or kpi_lower == "tth":
        kpi_name_clean = "Time-to-Hire"
    elif "onboarding" in kpi_lower or kpi_lower == "oct":
        kpi_name_clean = "Onboarding Completion Time"
    elif "offboarding" in kpi_lower or kpi_lower == "oft":
        kpi_name_clean = "Offboarding Completion Time"
    elif "attrition" in kpi_lower or kpi_lower == "ear":
        kpi_name_clean = "Employee Attrition Rate"
    elif "payroll" in kpi_lower or kpi_lower == "per":
        kpi_name_clean = "Payroll Error Rate"
        
    domain_key = get_domain_key(kpi_name_clean)
    lock = DOMAIN_LOCKS.get(domain_key)
    
    if lock:
        runtime_state.log_system_event(f"[bold yellow][Orchestrator][/bold yellow] Waiting to acquire lock for domain: [cyan]{domain_key}[/cyan]...")
        lock.acquire()
        
    try:
        handle_rca_workflow(kpi_name_clean, severity, year, month, **extra_params)
    finally:
        if lock:
            runtime_state.log_system_event(f"[bold yellow][Orchestrator][/bold yellow] Releasing lock for domain: [cyan]{domain_key}[/cyan]")
            lock.release()

def check_sap_breaches():
    """
    Fetches KPI data from SAP and routes any breaches to the correct domain agent.
    Can be run once manually or in a loop.
    """
    runtime_state.SYSTEM_STATE["status"] = "fetching"
    runtime_state.SYSTEM_STATE["last_checked"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    runtime_state.log_system_event("Checking OData endpoint for active breaches...")
    
    kpi_data = odata_client.fetch_kpi_data()
    
    if kpi_data:
        breach_found = False
        
        # Sort the kpi_data so Critical breaches (Critical=1) are processed first
        sorted_kpi_data = sorted(
            kpi_data, 
            key=lambda r: int(r.get("Critical", 0)) if r.get("Critical") is not None else 0, 
            reverse=True
        )
        
        threads = []
        for row in sorted_kpi_data:
            kpi_name = row.get("KPI_Name")
            
            # Check for the breach field (supporting both 'Breach' and 'KPI_Breach')
            breach_val = row.get("Breach") or row.get("KPI_Breach")
            if breach_val == "X":
                breach_found = True
                
                # Determine Severity based on fields
                is_critical = str(row.get("Critical")) == "1" or str(row.get("Severity")).lower() == "critical"
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
                
        if not breach_found:
            runtime_state.log_system_event("[dim]No breaches detected in this cycle.[/dim]")
            
    runtime_state.SYSTEM_STATE["status"] = "completed"
    time.sleep(2)
    runtime_state.SYSTEM_STATE["status"] = "idle"

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
        # Calculate time until next 9:00 AM and sleep
        now = datetime.datetime.now()
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        
        # If it's already past 9:00 AM today, target is 9:00 AM tomorrow
        if now >= target:
            target += datetime.timedelta(days=1)
            
        seconds_to_sleep = (target - now).total_seconds()
        
        hours = int(seconds_to_sleep // 3600)
        minutes = int((seconds_to_sleep % 3600) // 60)
        runtime_state.log_system_event(f"Sleeping until 9:00 AM. Next check scheduled at {target.strftime('%Y-%m-%d %H:%M:%S')} (in {hours}h {minutes}m).")
        
        # Sleep until the target time
        time.sleep(seconds_to_sleep)
        
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
