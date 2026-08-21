"""
config.py — Centralized configuration for the Databricks agents.

Loads environment variables from .env and validates that all
required settings are present before any agent can run.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from the project root
# ---------------------------------------------------------------------------
_project_root = Path(__file__).resolve().parent
_env_path = _project_root / ".env"

if _env_path.exists():
    load_dotenv(_env_path)
else:
    print(f"⚠️  No .env file found at {_env_path}. Assuming environment variables are provided by the host (e.g., Vercel/Docker).")

# ---------------------------------------------------------------------------
# Databricks connection
# ---------------------------------------------------------------------------
DATABRICKS_HOST: str = os.getenv("DATABRICKS_HOST", "").rstrip("/")
DATABRICKS_TOKEN: str = os.getenv("DATABRICKS_TOKEN", "")

# ---------------------------------------------------------------------------
# Notebook settings
# ---------------------------------------------------------------------------
NOTEBOOK_PATH: str = os.getenv("NOTEBOOK_PATH", "")

# ---------------------------------------------------------------------------
# Polling and Wait Configuration
# ---------------------------------------------------------------------------
ODATA_POLL_INTERVAL = int(os.getenv("ODATA_POLL_INTERVAL", "15"))
DATABRICKS_POLL_INTERVAL = int(os.getenv("DATABRICKS_POLL_INTERVAL", "15"))
MAX_WAIT_SECONDS = int(os.getenv("MAX_WAIT_SECONDS", "3600"))

# ---------------------------------------------------------------------------
# Databricks Configuration
# ---------------------------------------------------------------------------
DATABRICKS_HOST = os.getenv("DATABRICKS_HOST")
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN")
API_BASE_URL = f"{DATABRICKS_HOST}/api/2.1"
AUTH_HEADERS = {"Authorization": f"Bearer {DATABRICKS_TOKEN}"}
EXISTING_CLUSTER_ID = os.getenv("EXISTING_CLUSTER_ID")

# Domain Agent Specific Configuration
# ---------------------------------------------------------------------------
RECRUITMENT_NOTEBOOK_PATH = os.getenv("RECRUITMENT_NOTEBOOK_PATH")
ONBOARDING_NOTEBOOK_PATH = os.getenv("ONBOARDING_NOTEBOOK_PATH")
OFFBOARDING_NOTEBOOK_PATH = os.getenv("OFFBOARDING_NOTEBOOK_PATH")
EMPLOYEE_CENTRAL_NOTEBOOK_PATH = os.getenv("EMPLOYEE_CENTRAL_NOTEBOOK_PATH")
EMPLOYEE_PAYMENT_NOTEBOOK_PATH = os.getenv("EMPLOYEE_PAYMENT_NOTEBOOK_PATH")

RECRUITMENT_RESULTS_TABLE = os.getenv("RECRUITMENT_RESULTS_TABLE")
ONBOARDING_RESULTS_TABLE = os.getenv("ONBOARDING_RESULTS_TABLE")
OFFBOARDING_RESULTS_TABLE = os.getenv("OFFBOARDING_RESULTS_TABLE")
EMPLOYEE_CENTRAL_RESULTS_TABLE = os.getenv("EMPLOYEE_CENTRAL_RESULTS_TABLE")
EMPLOYEE_PAYMENT_RESULTS_TABLE = os.getenv("EMPLOYEE_PAYMENT_RESULTS_TABLE")

# Databricks SQL configuration
# ---------------------------------------------------------------------------
DATABRICKS_SQL_HTTP_PATH: str = os.getenv("DATABRICKS_SQL_HTTP_PATH", "")

# ---------------------------------------------------------------------------
# KPI Correlation Matrix (Single Main Breach Analysis)
# ---------------------------------------------------------------------------
INTERDEPENDENCY_MATRIX = {
    "Time-to-Hire": {
        "affected_by": [],
        "affects": ["Onboarding Completion Time"],
        "correlated_with": ["Employee Attrition Rate"]
    },
    "Onboarding Completion Time": {
        "affected_by": ["Time-to-Hire"],
        "affects": ["Employee Attrition Rate"],
        "correlated_with": ["Offboarding Completion Time"]
    },
    "Employee Attrition Rate": {
        "affected_by": ["Onboarding Completion Time"],
        "affects": ["Offboarding Completion Time"],
        "correlated_with": ["Time-to-Hire"]
    },
    "Offboarding Completion Time": {
        "affected_by": ["Employee Attrition Rate"],
        "affects": [],
        "correlated_with": ["Onboarding Completion Time"]
    }
}


# OData Configuration
ODATA_URL = os.getenv("ODATA_URL")

# OAuth2 Configuration (for SAP Datasphere API Access)
# .strip() prevents errors caused by accidental quotes/spaces in .env file values
OAUTH_TOKEN_URL = os.getenv("OAUTH_TOKEN_URL", "").strip().strip('"').strip("'")
OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "").strip().strip('"').strip("'")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "").strip().strip('"').strip("'")

# Path to the shared state file that both agents read/write
STATE_FILE: Path = _project_root / "run_state.json"

# Active breach tracking
ACTIVE_BREACHES_FILE: Path = _project_root / "active_breaches.json"
NOTIFICATION_COOLDOWN_MINUTES: int = int(os.getenv("NOTIFICATION_COOLDOWN_MINUTES", "0"))  # Default: 0 minutes (no cooldown)

# LLM Configuration
# ---------------------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")


# Fallback Chain
PRIMARY_PROVIDER = os.getenv("PRIMARY_PROVIDER", "gemini")
PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "gemini-3.5-flash")

SECONDARY_PROVIDER = os.getenv("SECONDARY_PROVIDER", "gemini")
SECONDARY_MODEL = os.getenv("SECONDARY_MODEL", "gemini-3.6-flash")

THIRD_PROVIDER = os.getenv("THIRD_PROVIDER", "gemini")
THIRD_MODEL = os.getenv("THIRD_MODEL", "gemini-2.5-pro")

FOURTH_PROVIDER = os.getenv("FOURTH_PROVIDER", "groq")
FOURTH_MODEL = os.getenv("FOURTH_MODEL", "openai/gpt-oss-120b")

# Notification Configuration
# ---------------------------------------------------------------------------
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")

TA_HEAD_EMAIL = os.getenv("TA_HEAD_EMAIL")
HRBP_EMAIL = os.getenv("HRBP_EMAIL")
CHRO_EMAIL = os.getenv("CHRO_EMAIL")
PAYROLL_MANAGER_EMAIL = os.getenv("PAYROLL_MANAGER_EMAIL")
CFO_EMAIL = os.getenv("CFO_EMAIL")

TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL")


def validate() -> None:
    """Verify that all required configuration is present. Exit on failure."""
    errors: list[str] = []

    if not DATABRICKS_HOST:
        errors.append("DATABRICKS_HOST is not set")
    if not DATABRICKS_TOKEN:
        errors.append("DATABRICKS_TOKEN is not set")
    if not DATABRICKS_SQL_HTTP_PATH:
        errors.append("DATABRICKS_SQL_HTTP_PATH is not set (Required for querying results)")

    if errors:
        print("❌ Configuration errors:")
        for err in errors:
            print(f"   • {err}")
        print("\n   Please update your .env file and try again.")
        sys.exit(1)
