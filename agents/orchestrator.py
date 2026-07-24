import time
import threading
from rich.console import Console
from rich.panel import Panel

# Import config and client from the parent root directory
import config
from clients import odata_client

# Relative imports for other agents in the same folder
from agents import recruitment_agent
from agents import onboarding_offboarding_agent
from agents import employee_central_agent
from agents import employee_payment_agent
from agents import coordinator_agent
from agents import prescriptive_agent

console = Console()

# Threading locks to serialize writes to the same Databricks tables per domain
DOMAIN_LOCKS = {
    "recruitment": threading.Lock(),
    "onboarding_offboarding": threading.Lock(),
    "employee_central": threading.Lock(),
    "employee_payment": threading.Lock()
}

def get_domain_key(kpi_name):
    kpi_lower = kpi_name.lower()
    if "time-to-hire" in kpi_lower or "time to hire" in kpi_lower:
        return "recruitment"
    elif "onboarding" in kpi_lower or "offboarding" in kpi_lower:
        return "onboarding_offboarding"
    elif "attrition" in kpi_lower:
        return "employee_central"
    elif "payroll" in kpi_lower:
        return "employee_payment"
    return None

def resolve_agent_info(kpi_name):
    kpi_lower = kpi_name.lower()
    if "time-to-hire" in kpi_lower or "time to hire" in kpi_lower:
        return recruitment_agent, config.RECRUITMENT_RESULTS_TABLE
    elif "onboarding" in kpi_lower:
        return onboarding_offboarding_agent, config.ONBOARDING_OFFBOARDING_RESULTS_TABLE
    elif "offboarding" in kpi_lower:
        return onboarding_offboarding_agent, config.ONBOARDING_OFFBOARDING_RESULTS_TABLE
    elif "attrition" in kpi_lower:
        return employee_central_agent, config.EMPLOYEE_CENTRAL_RESULTS_TABLE
    elif "payroll" in kpi_lower:
        return employee_payment_agent, config.EMPLOYEE_PAYMENT_RESULTS_TABLE
    return None, None

def handle_rca_workflow(primary_kpi_name, severity, year, month, **extra_params):
    """
    Two-Stage Sequential Workflow for Incident Cascading.
    Stage 1: Trigger primary notebook (Main Breach) and wait.
    Stage 2: Trigger dependency notebooks (Dependency Breach) and wait.
    Passes combined R (result) and D (dependency) tables to the LLM.
    """
    console.print(f"[bold red]!! BREACH DETECTED !![/bold red] Processing incident: {primary_kpi_name} (Severity: {severity}, Period: {year}-{month})")
    
    matrix_entry = config.INTERDEPENDENCY_MATRIX.get(primary_kpi_name, {})
    affects = matrix_entry.get("affects", [])
    correlated_with = matrix_entry.get("correlated_with", [])
    
    # -------------------------------------------------------------------------
    # STAGE 1: Main Breach (MB)
    # -------------------------------------------------------------------------
    console.print(f"[bold cyan]STAGE 1 (Main Breach):[/bold cyan] Triggering Primary Databricks Notebook for {primary_kpi_name}...")
    
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
    else:
        console.print(f"[bold red]Failed to find primary agent mapped for {primary_kpi_name}[/bold red]")
        return
        
    if not mb_run_info_list:
        console.print("[red]Failed to trigger primary Databricks job.[/red]")
        return
        
    console.print(f"[bold cyan]STAGE 1:[/bold cyan] Waiting for Main Breach job to complete...")
    mb_context = coordinator_agent.wait_and_fetch_results_batch(
        mb_run_info_list, 
        primary_kpi_name, 
        severity, 
        year, 
        month
    )
    
    if not mb_context:
        console.print("[red]Main Breach execution failed. Aborting dependency cascade.[/red]")
        return
        
    # -------------------------------------------------------------------------
    # STAGE 2: Dependency Breach (DB)
    # -------------------------------------------------------------------------
    db_run_info_list = []
    if affects:
        console.print(f"[bold cyan]STAGE 2 (Dependency):[/bold cyan] Triggering {len(affects)} Dependency Notebooks simultaneously...")
        
        # Acquire locks for each dependency domain before running
        dep_locks_acquired = []
        for dep_kpi in affects:
            dep_domain = get_domain_key(dep_kpi)
            d_lock = DOMAIN_LOCKS.get(dep_domain)
            if d_lock:
                console.print(f"[bold yellow][Orchestrator][/bold yellow] Acquiring dependency lock for domain: [cyan]{dep_domain}[/cyan]...")
                d_lock.acquire()
                dep_locks_acquired.append((dep_domain, d_lock))
                
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
                else:
                    console.print(f"[yellow]Warning: No agent mapped for dependency {target_kpi}[/yellow]")
                    
            threads = []
            for dep_kpi in affects:
                t = threading.Thread(target=trigger_dependency_agent, args=(dep_kpi,))
                t.start()
                threads.append(t)
                
            for t in threads:
                t.join()
                
            if db_run_info_list:
                console.print(f"[bold cyan]STAGE 2:[/bold cyan] Waiting for {len(db_run_info_list)} Dependency jobs to complete...")
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
            # Release dependency locks
            for dep_domain, d_lock in dep_locks_acquired:
                console.print(f"[bold yellow][Orchestrator][/bold yellow] Releasing dependency lock for domain: [cyan]{dep_domain}[/cyan]")
                d_lock.release()
    else:
        db_context = ""
        console.print("[dim]No dependencies to trigger for this KPI.[/dim]")
        
    # -------------------------------------------------------------------------
    # STAGE 3: Combine & Prescribe
    # -------------------------------------------------------------------------
    full_combined_context = f"{mb_context}\n\n{db_context}" if db_context else mb_context
    
    prescriptive_agent.run_prescriptive_analysis(
        full_combined_context, 
        primary_kpi_name,
        correlated_with=correlated_with
    )

def route_kpi_breach(kpi_name, severity, year, month, **extra_params):
    """Wrapper to start the incident workflow."""
    # Normalize KPI Name
    kpi_name_clean = kpi_name.strip()
    kpi_lower = kpi_name_clean.lower()
    
    # Map common variations to standard matrix/config keys
    if "time to hire" in kpi_lower or "time-to-hire" in kpi_lower:
        kpi_name_clean = "Time-to-Hire"
    elif "onboarding" in kpi_lower:
        kpi_name_clean = "Onboarding Completion Time"
    elif "offboarding" in kpi_lower:
        kpi_name_clean = "Offboarding Completion Time"
    elif "attrition" in kpi_lower:
        kpi_name_clean = "Employee Attrition Rate"
    elif "payroll" in kpi_lower:
        kpi_name_clean = "Payroll Error Rate"
        
    domain_key = get_domain_key(kpi_name_clean)
    lock = DOMAIN_LOCKS.get(domain_key)
    
    if lock:
        console.print(f"[bold yellow][Orchestrator][/bold yellow] Waiting to acquire lock for domain: [cyan]{domain_key}[/cyan]...")
        lock.acquire()
        
    try:
        handle_rca_workflow(kpi_name_clean, severity, year, month, **extra_params)
    finally:
        if lock:
            console.print(f"[bold yellow][Orchestrator][/bold yellow] Releasing lock for domain: [cyan]{domain_key}[/cyan]")
            lock.release()

def check_sap_breaches():
    """
    Fetches KPI data from SAP and routes any breaches to the correct domain agent.
    Can be run once manually or in a loop.
    """
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
                
                console.print(Panel(f"🎯 Triggering Domain Agent dynamically...\nKPI: [bold]{kpi_name}[/bold]\nSeverity: [bold]{severity}[/bold]\nYear: [bold]{year}[/bold]\nMonth: [bold]{month}[/bold]\nParams: {extra_params}", title="Agent Trigger Payload", border_style="green"))
                
                # Run each breach in parallel; safety locks inside route_kpi_breach serialize runs on the same domain
                t = threading.Thread(target=route_kpi_breach, args=(kpi_name, severity, year, month), kwargs=extra_params)
                t.start()
                threads.append(t)
                
        # Wait for all simultaneous breach threads to finish before returning.
        # This is CRITICAL for Vercel, so it keeps the CPU awake until all are done.
        for t in threads:
            t.join()
                
        if not breach_found:
            console.print("[dim]No breaches detected in this cycle.[/dim]")


def run_orchestrator():
    """
    Master Orchestrator Loop:
    Continuously polls the OData service and routes breaches.
    """
    console.print(Panel("[bold blue]Starting Master Orchestrator Agent[/bold blue]\nMonitoring OData endpoint for KPI breaches..."))
    
    while True:
        check_sap_breaches()
        
        # Wait before polling again based on config
        console.print(f"[dim]Sleeping for {config.ODATA_POLL_INTERVAL} seconds...[/dim]")
        time.sleep(config.ODATA_POLL_INTERVAL)

if __name__ == "__main__":
    try:
        run_orchestrator()
    except KeyboardInterrupt:
        console.print("\n[bold red]Orchestrator stopped by user.[/bold red]")
