import datetime
import re
from rich.console import Console

console = Console()

# Shared runtime state for the orchestrator
SYSTEM_STATE = {
    "status": "idle",             # idle, fetching, running_stage_1, running_stage_2, prescribing, completed
    "last_checked": None,
    "primary_agent": None,        # tracks the primary breach agent (domain_key)
    "current_breaches": [],        # list of active breaches being processed
    "active_runs": [],             # list of active Databricks job runs with ID, KPI, and stage
    "agent_statuses": {            # recruitment, onboarding, offboarding, central, payment
        "recruitment": "inactive", # inactive, running, completed, failed
        "onboarding": "inactive",
        "offboarding": "inactive",
        "employee_central": "inactive",
        "employee_payment": "inactive"
    },
    "system_logs": []              # recent 50 console events
}

def log_system_event(message: str):
    """Utility to append a message to the shared system logs and print it."""
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    # Clean rich formatting tags for web logs
    clean_msg = re.sub(r'\[/?[a-zA-Z0-9 _=#,.-]+\]', '', message)
    log_entry = f"[{timestamp}] {clean_msg}"
    SYSTEM_STATE["system_logs"].append(log_entry)
    if len(SYSTEM_STATE["system_logs"]) > 50:
        SYSTEM_STATE["system_logs"].pop(0)
    console.print(message)
