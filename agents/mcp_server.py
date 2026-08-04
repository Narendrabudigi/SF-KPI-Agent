import json
import os
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    from  mcp.server.fastmcp import FastMCP
import runtime_state

# Import existing clients and agent modules
from clients import odata_client
from clients import databricks_client
from clients import databricks_sql_client
from clients import notification_client

# Domain agent modules
from agents import recruitment_agent
from agents import onboarding_agent
from agents import offboarding_agent
from agents import employee_central_agent
from agents import employee_payment_agent

mcp = FastMCP("HR-KPI-Agent-Server")
MEMORY_FILE = "mcp_memory.json"

def _read_memory() -> dict:
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def _write_memory(data: dict):
    with open(MEMORY_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ---------------------------------------------------------------------------
# Tools: Memory Reads & Writes
# ---------------------------------------------------------------------------

@mcp.tool()
def store_incident_context(incident_id: str, context_data: str) -> str:
    """
    Saves root cause findings and context data for a specific incident.
    """
    mem = _read_memory()
    mem[incident_id] = context_data
    _write_memory(mem)
    return f"Successfully stored context for incident: {incident_id}"

@mcp.tool()
def get_incident_context(incident_id: str) -> str:
    """
    Retrieves stored root cause context data for an incident.
    """
    mem = _read_memory()
    return mem.get(incident_id, f"No context found for incident: {incident_id}")

@mcp.tool()
def store_odata_row(incident_id: str, row_data: str) -> str:
    """
    Saves the original OData SAP row (as JSON string) for a specific incident.
    Used to enrich context with employee/candidate-level details from SuccessFactors,
    which the prescriptive LLM agent retrieves and injects into its prompt.
    """
    mem = _read_memory()
    key = f"odata_row_{incident_id}"
    mem[key] = row_data
    _write_memory(mem)
    return f"Successfully stored OData row for incident: {incident_id}"

@mcp.tool()
def get_odata_row(incident_id: str) -> str:
    """
    Retrieves the original OData SAP row JSON for a specific incident.
    Used by prescriptive_agent to inject employee/candidate-level detail into the LLM prompt.
    """
    mem = _read_memory()
    key = f"odata_row_{incident_id}"
    return mem.get(key, f"No OData row found for incident: {incident_id}")

# ---------------------------------------------------------------------------
# Tools: Domain Agents Execution
# ---------------------------------------------------------------------------

@mcp.tool()
def execute_domain_agent(
    domain: str,
    kpi_name: str,
    severity: str,
    year: str,
    month: str,
    primary_kpi: str = None,
    kpi_id: str = None,
    extra_params_json: str = None
) -> str:
    """
    Submits a Databricks Job run for the specified domain agent.

    Args:
        domain: Domain key (recruitment, onboarding, offboarding, employee_central, employee_payment)
        kpi_name: The name of the KPI being analyzed
        severity: Breach severity (Critical / Warning)
        year: Breach year
        month: Breach month
        primary_kpi: The primary breach KPI (for cascade scenarios)
        kpi_id: The unique KPI/process ID for this specific breach record (optional)
        extra_params_json: JSON string of all OData row parameters.
                           These are forwarded as **kwargs to the Databricks notebook.

    Returns:
        str: Databricks Run ID if successful.
    """
    agent_map = {
        "recruitment": recruitment_agent,
        "onboarding": onboarding_agent,
        "offboarding": offboarding_agent,
        "employee_central": employee_central_agent,
        "employee_payment": employee_payment_agent
    }
    agent = agent_map.get(domain.lower())
    if not agent:
        raise ValueError(f"Unknown domain agent: {domain}")

    # Parse all extra OData params from JSON string
    extra_params = {}
    if extra_params_json:
        try:
            extra_params = json.loads(extra_params_json)
            runtime_state.log_system_event(
                f"[dim][MCP] execute_domain_agent: forwarding {len(extra_params)} extra params to '{domain}' agent[/dim]"
            )
        except Exception as e:
            runtime_state.log_system_event(f"[yellow]⚠️ [MCP] Could not parse extra_params_json: {e}[/yellow]")

    # Explicitly inject kpi_id if provided and not already in extra_params
    if kpi_id and "kpi_id" not in extra_params and "KPI_ID" not in extra_params:
        extra_params["KPI_ID"] = kpi_id

    run_id = agent.run_agent(kpi_name, severity, year, month, primary_kpi=primary_kpi, **extra_params)
    if not run_id:
        raise RuntimeError(f"Agent failed to submit job for {kpi_name}")
    return str(run_id)

@mcp.tool()
def get_job_status(run_id: int) -> str:
    """
    Checks lifecycle status of a Databricks run ID.
    """
    status = databricks_client.get_run_status(run_id)
    return json.dumps(status)

@mcp.tool()
def query_results_table(table_name: str, primary_kpi_filter: str = None) -> str:
    """
    Queries raw rows from the Databricks SQL results table.
    Optionally filters rows by a primary KPI name (PRIMARY_BREACH_KPI column).
    Returns empty JSON array if the table has no results (safe fallback).
    """
    try:
        df = databricks_sql_client.fetch_results(table_name)
        if df.empty:
            runtime_state.log_system_event(
                f"[yellow]⚠️ [MCP] query_results_table: '{table_name}' returned 0 rows.[/yellow]"
            )
            return json.dumps([])

        # Apply optional filter by primary KPI name
        if primary_kpi_filter:
            primary_col = next((c for c in df.columns if c.upper() == "PRIMARY_BREACH_KPI"), None)
            if primary_col:
                df = df[df[primary_col] == primary_kpi_filter]
                runtime_state.log_system_event(
                    f"[dim][MCP] Filtered '{table_name}' by PRIMARY_BREACH_KPI='{primary_kpi_filter}': {len(df)} rows[/dim]"
                )

        return df.to_json(orient="records")
    except Exception as e:
        runtime_state.log_system_event(f"[red]❌ [MCP] query_results_table error for '{table_name}': {e}[/red]")
        return json.dumps({"error": str(e)})

# ---------------------------------------------------------------------------
# Tools: Core Pipelines
# ---------------------------------------------------------------------------

@mcp.tool()
def trigger_odata_check() -> str:
    """
    Runs SuccessFactors OData breach fetching.
    """
    try:
        from agents.orchestrator import check_sap_breaches
        check_sap_breaches()
        return "OData breach check cycle completed."
    except Exception as e:
        return f"Error: {e}"
