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


def handle_batch_rca_workflow(domain_key, breach_rows_list, kpi_name, year, month):
    """
    Batch RCA Workflow — processes ALL breaches for a single domain in ONE Databricks run.
    
    Flow:
      1. Build BREACH_LIST JSON from all breach rows: [{"KPI_ID": "...", "Severity": "..."}, ...]
      2. Trigger Databricks ONCE for this domain with the batch parameter.
      3. Wait for the single run to complete.
      4. Fetch results and filter by analysis_type:
         - ACTUAL_BREACH rows → sent to prescriptive agent
         - PREDICTIVE_RISK rows → logged only
      5. Run prescriptive analysis ONCE with only ACTUAL_BREACH data.
      6. Send ONE consolidated notification covering all actual breaches.
    
    Args:
        domain_key: Domain identifier (e.g., "offboarding", "recruitment")
        breach_rows_list: List of dicts, each being an OData breach row for this domain
        kpi_name: Normalized KPI name for this domain
        year: Breach year
        month: Breach month
    """
    # Determine the highest severity among all breaches in this batch
    severities = []
    for row in breach_rows_list:
        is_critical = str(row.get("Critical")) == "1" or str(row.get("Severity", "")).lower() == "critical"
        severities.append("Critical" if is_critical else "Warning")
    # Use the worst severity for the batch
    batch_severity = "Critical" if "Critical" in severities else "Warning"
    
    breach_count = len(breach_rows_list)
    runtime_state.log_system_event(
        f"[bold red]!! BATCH BREACH DETECTED !![/bold red] Processing {breach_count} breach(es) for domain: "
        f"[bold]{domain_key}[/bold] / {kpi_name} (Batch Severity: {batch_severity}, Period: {year}-{month})"
    )
    
    runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "running"
    runtime_state.SYSTEM_STATE["primary_agent"] = domain_key
    runtime_state.SYSTEM_STATE["status"] = f"analyzing_{domain_key}"
    
    breach_key = f"{kpi_name} ({year}-{month}) [{breach_count} breaches]"
    if breach_key not in runtime_state.SYSTEM_STATE["current_breaches"]:
        runtime_state.SYSTEM_STATE["current_breaches"].append(breach_key)
    
    matrix_entry = config.INTERDEPENDENCY_MATRIX.get(kpi_name, {})
    correlated_with = matrix_entry.get("correlated_with", [])
    
    # -------------------------------------------------------------------------
    # Build BREACH_LIST — the batch parameter for Databricks
    # For standard KPIs: [{"KPI_ID": "OFB100002", "Severity": "WARNING"}, ...]
    # For Employee Attrition Rate: [{"Department": "Sales", "Severity": "WARNING", "Year": "2026", "Month": "07"}, ...]
    # -------------------------------------------------------------------------
    breach_list = []
    all_odata_rows = []  # Collect all OData rows for MCP memory and prescriptive context
    
    is_attrition = (domain_key == "employee_central") or breach_tracker._is_attrition_kpi(kpi_name)
    
    for row in breach_rows_list:
        is_critical = str(row.get("Critical")) == "1" or str(row.get("Severity", "")).lower() == "critical"
        sev = "CRITICAL" if is_critical else "WARNING"
        
        if is_attrition:
            dept = breach_tracker._extract_department(row) or row.get("DEPARTMENT_ID") or row.get("Department") or ""
            row_year = row.get("YEAR") or row.get("Year") or row.get("Posting_Year") or year
            row_month = row.get("MONTH") or row.get("Month") or row.get("Posting_Month") or month
            
            month_str = str(row_month) if row_month is not None else str(month)
            if month_str.isdigit():
                month_str = str(int(month_str))

            year_str = str(row_year) if row_year is not None else str(year)

            breach_entry = {
                "DEPARTMENT_ID": str(dept),
                "YEAR": year_str,
                "MONTH": month_str,
                "Severity": sev
            }
        else:
            kpi_id = (
                row.get("KPI_ID") or row.get("KPI_Id") or row.get("kpi_id") or
                row.get("Kpi_Id") or row.get("KPI_ID_Col") or
                row.get("OFFBOARDING_PROCESS_ID") or row.get("ONBOARDING_PROCESS_ID") or
                row.get("JOB_REQ_ID")
            )
            breach_entry = {"KPI_ID": str(kpi_id) if kpi_id else "", "Severity": sev}

        breach_list.append(breach_entry)
        
        # Collect cleaned extra params for each row
        extra_params = {}
        for k, v in row.items():
            if v is None or v == "" or str(v).upper() in ("NULL", "NONE"):
                continue
            extra_params[k] = v
        all_odata_rows.append(extra_params)
    
    breach_list_json = json.dumps(breach_list)
    runtime_state.log_system_event(
        f"[bold cyan]BATCH TRIGGER:[/bold cyan] Sending {breach_count} breaches to Databricks: {breach_list_json}"
    )
    
    # -------------------------------------------------------------------------
    # STAGE 1: Single Databricks Run (Batch Mode)
    # -------------------------------------------------------------------------
    runtime_state.log_system_event(
        f"[bold cyan]STAGE 1 (Batch Main Breach):[/bold cyan] Triggering SINGLE Databricks Notebook for {kpi_name} with {breach_count} breach(es)..."
    )
    
    agent_module, table_name = resolve_agent_info(kpi_name)
    mb_context = None
    
    if not agent_module:
        runtime_state.log_system_event(f"[bold red]Failed to find primary agent mapped for {kpi_name}[/bold red]")
        runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "failed"
        return
    
    # Build a combined incident_id for MCP memory
    batch_incident_id = f"{kpi_name.replace(' ', '_')}_{year}_{month}_batch"
    
    # Store all OData rows in MCP memory for prescriptive agent enrichment
    mcp_client.call_tool("store_odata_row", {
        "incident_id": batch_incident_id,
        "row_data": json.dumps(all_odata_rows)
    })
    
    primary_lock = DOMAIN_LOCKS.get(domain_key)
    if primary_lock:
        runtime_state.log_system_event(f"[bold yellow][Orchestrator][/bold yellow] Acquiring primary lock for domain: [cyan]{domain_key}[/cyan]...")
        primary_lock.acquire()
    
    run_id = None
    try:
        # Use the first row's extra_params as the base for common fields (year, month, etc.)
        # but the BREACH_LIST param carries all individual breach IDs + severities
        base_extra_params = all_odata_rows[0] if all_odata_rows else {}
        extra_params_json = json.dumps(base_extra_params)
        
        run_id_str = mcp_client.call_tool("execute_domain_agent", {
            "domain": domain_key,
            "kpi_name": kpi_name,
            "severity": batch_severity,
            "year": str(year),
            "month": str(month),
            "primary_kpi": kpi_name,
            "kpi_id": None,  # Not applicable in batch mode — all IDs are in BREACH_LIST
            "breach_list_json": breach_list_json,
            "extra_params_json": extra_params_json
        })
        run_id = int(run_id_str) if (isinstance(run_id_str, str) and run_id_str.isdigit()) else run_id_str
        
        if run_id:
            mb_run_info_list = [{
                "run_id": run_id,
                "table_name": table_name,
                "kpi_name": kpi_name,
                "is_primary": True
            }]
            
            # Determine notebook path for state tracking
            notebook_path = (
                config.RECRUITMENT_NOTEBOOK_PATH if "recruitment" in agent_module.__name__ else (
                    config.ONBOARDING_NOTEBOOK_PATH if "onboarding" in agent_module.__name__ else (
                        config.OFFBOARDING_NOTEBOOK_PATH if "offboarding" in agent_module.__name__ else (
                            config.EMPLOYEE_CENTRAL_NOTEBOOK_PATH if "central" in agent_module.__name__ else (
                                config.EMPLOYEE_PAYMENT_NOTEBOOK_PATH if "payment" in agent_module.__name__ else "Unknown"
                            )
                        )
                    )
                )
            )
            
            runtime_state.SYSTEM_STATE["active_runs"].append({
                "run_id": run_id,
                "kpi_name": kpi_name,
                "primary_kpi": kpi_name,
                "notebook_path": notebook_path,
                "stage": f"Stage 1 (Batch Analysis — {breach_count} breaches)"
            })
            
            runtime_state.log_system_event(f"[bold cyan]STAGE 1:[/bold cyan] Waiting for Batch Main Breach job to complete...")
            mb_context = mcp_client.call_tool("wait_and_fetch_results_batch", {
                "run_info_list_json": json.dumps(mb_run_info_list),
                "primary_kpi_name": kpi_name,
                "severity": batch_severity,
                "year": str(year),
                "month": str(month)
            })
    finally:
        if run_id:
            runtime_state.SYSTEM_STATE["active_runs"] = [
                r for r in runtime_state.SYSTEM_STATE["active_runs"] if r["run_id"] != run_id
            ]
        if primary_lock:
            runtime_state.log_system_event(f"[bold yellow][Orchestrator][/bold yellow] Releasing primary lock for domain: [cyan]{domain_key}[/cyan]")
            primary_lock.release()
    
    if not mb_context:
        runtime_state.log_system_event("[red]Batch Main Breach execution failed or timed out. Aborting analysis.[/red]")
        runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "failed"
        return
    
    # -------------------------------------------------------------------------
    # Parse the structured context from coordinator agent
    # mb_context is a JSON string from MCP tool — parse it to extract actual/predictive separation
    # -------------------------------------------------------------------------
    actual_breach_context = None
    predictive_risk_context = None
    full_combined_context = None
    
    if isinstance(mb_context, str):
        try:
            context_dict = json.loads(mb_context)
            actual_breach_context = context_dict.get("actual_breach_context")
            predictive_risk_context = context_dict.get("predictive_risk_context")
            full_combined_context = context_dict.get("full_context", mb_context)
        except (json.JSONDecodeError, TypeError):
            # If it's a plain string (backward compat from old coordinator), treat as full context
            full_combined_context = mb_context
            actual_breach_context = mb_context
    elif isinstance(mb_context, dict):
        actual_breach_context = mb_context.get("actual_breach_context")
        predictive_risk_context = mb_context.get("predictive_risk_context")
        full_combined_context = mb_context.get("full_context", str(mb_context))
    else:
        full_combined_context = str(mb_context)
        actual_breach_context = full_combined_context
    
    # -------------------------------------------------------------------------
    # STAGE 2: Prescriptive Analysis (ONLY ACTUAL_BREACH context to LLM)
    # -------------------------------------------------------------------------
    runtime_state.SYSTEM_STATE["status"] = "prescribing"
    
    # Use only ACTUAL_BREACH context for prescriptive agent
    prescriptive_context = actual_breach_context or full_combined_context
    
    runtime_state.log_system_event(
        f"[bold magenta]Prescriptive Analysis:[/bold magenta] Sending ONLY ACTUAL_BREACH data to prescriptive agent for {breach_count} breach(es)..."
    )
    
    if predictive_risk_context:
        runtime_state.log_system_event(
            f"[bold yellow]ℹ️  PREDICTIVE_RISK data excluded from prescriptive agent (logged for dashboard only)[/bold yellow]"
        )
    
    # Store incident context in MCP Memory
    mcp_client.call_tool("store_incident_context", {"incident_id": batch_incident_id, "context_data": prescriptive_context})
    
    # Build breach summaries for consolidated notification
    breach_summaries = []
    for i, row in enumerate(breach_rows_list):
        if is_attrition:
            dept = breach_tracker._extract_department(row) or row.get("Department") or f"Dept-{i}"
            row_year = row.get("Year") or row.get("Posting_Year") or year
            row_month = row.get("Month") or row.get("Posting_Month") or month
            breach_summaries.append({
                "kpi_id": str(dept),
                "department": str(dept),
                "kpi_name": kpi_name,
                "severity": severities[i] if i < len(severities) else batch_severity,
                "year": str(row_year) if row_year else "",
                "month": str(row_month) if row_month else ""
            })
        else:
            kpi_id = (
                row.get("KPI_ID") or row.get("KPI_Id") or row.get("kpi_id") or
                row.get("Kpi_Id") or row.get("KPI_ID_Col") or
                row.get("OFFBOARDING_PROCESS_ID") or row.get("ONBOARDING_PROCESS_ID") or
                row.get("JOB_REQ_ID")
            )
            breach_summaries.append({
                "kpi_id": str(kpi_id) if kpi_id else f"Unknown-{i}",
                "kpi_name": kpi_name,
                "severity": severities[i] if i < len(severities) else batch_severity
            })
    
    mcp_client.call_tool("run_prescriptive_analysis", {
        "incident_id": batch_incident_id,
        "kpi_name": kpi_name,
        "interdependency_info_json": json.dumps(matrix_entry) if matrix_entry else None,
        "correlated_with_json": json.dumps(matrix_entry.get("correlated_with", [])) if matrix_entry.get("correlated_with") else None,
        "severity": batch_severity,
        "row_json": json.dumps(all_odata_rows),
        "breach_summaries_json": json.dumps(breach_summaries)
    })
    
    if runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] != "failed":
        runtime_state.SYSTEM_STATE["agent_statuses"][domain_key] = "completed"

    # Remove from active breaches
    if breach_key in runtime_state.SYSTEM_STATE["current_breaches"]:
        runtime_state.SYSTEM_STATE["current_breaches"].remove(breach_key)


def check_sap_breaches():
    """
    Fetches KPI data from SAP and routes breaches using BATCH MODE:
    1. Collects ALL breaches from OData
    2. Groups them by domain (e.g., all offboarding breaches together)
    3. Triggers Databricks ONCE per domain with a BREACH_LIST parameter
    4. Processes results, filters ACTUAL_BREACH only for prescriptive agent
    5. Sends ONE consolidated email per domain
    """
    runtime_state.SYSTEM_STATE["primary_agent"] = None
    runtime_state.SYSTEM_STATE["status"] = "fetching"
    runtime_state.SYSTEM_STATE["last_checked"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    runtime_state.log_system_event("Checking OData endpoint for active breaches...")
    
    kpi_data = odata_client.fetch_kpi_data()
    
    if kpi_data:
        breach_found = False
        current_active_ids = set()  # Track all active breach IDs for reconciliation
        
        # =====================================================================
        # PHASE 1: Collect — gather all breach rows grouped by domain
        # =====================================================================
        domain_breach_groups = {}  # domain_key → list of (kpi_name, row) tuples
        
        for row in kpi_data:
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
                
                # Determine domain
                domain_key = get_domain_key(kpi_name)
                if not domain_key:
                    runtime_state.log_system_event(f"[bold red]Unknown domain for KPI: {kpi_name}. Skipping.[/bold red]")
                    continue
                
                # Add to domain group
                if domain_key not in domain_breach_groups:
                    domain_breach_groups[domain_key] = {
                        "kpi_name": kpi_name,
                        "rows": [],
                        "year": row.get("Year") or row.get("Posting_Year"),
                        "month": row.get("Month") or row.get("Posting_Month")
                    }
                domain_breach_groups[domain_key]["rows"].append(row)
        
        # =====================================================================
        # PHASE 2: Batch Trigger — one Databricks run per domain
        # =====================================================================
        if domain_breach_groups:
            summary_list = [f"{k} ({len(v['rows'])} breaches)" for k, v in domain_breach_groups.items()]
            runtime_state.log_system_event(
                f"[bold green]📦 Collected breaches across {len(domain_breach_groups)} domain(s): "
                f"{', '.join(summary_list)}[/bold green]"
            )
            
            threads = []
            for domain_key, group in domain_breach_groups.items():
                runtime_state.log_system_event(
                    f"🎯 Triggering Batch Domain Agent for: [bold]{domain_key}[/bold] "
                    f"({len(group['rows'])} breach(es))"
                )
                
                t = threading.Thread(
                    target=handle_batch_rca_workflow,
                    args=(domain_key, group["rows"], group["kpi_name"], group["year"], group["month"])
                )
                t.start()
                threads.append(t)
            
            # Wait for all domain batch threads to finish
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
