import json
import os
from mcp.server.fastmcp import FastMCP
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
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_FILE = os.path.join(BASE_DIR, "mcp_memory.json")

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

# ---------------------------------------------------------------------------
# Tools: Domain Agents Execution
# ---------------------------------------------------------------------------

@mcp.tool()
def execute_domain_agent(domain: str, kpi_name: str, severity: str, year: str, month: str, primary_kpi: str = None, kpi_id: str = None, extra_params_json: str = None) -> str:
    """
    Submits a Databricks Job run for the specified domain agent.
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
    
    # Load dynamic extra parameters if provided
    extra_params = {}
    if extra_params_json:
        try:
            extra_params = json.loads(extra_params_json)
        except Exception as e:
            runtime_state.log_system_event(f"Error parsing extra_params_json: {e}")
            
    if kpi_id:
        extra_params["kpi_id"] = kpi_id
    
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
def query_results_table(table_name: str) -> str:
    """
    Queries raw rows from the Databricks SQL results table.
    """
    df = databricks_sql_client.fetch_results(table_name)
    return df.to_json(orient="records")

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
