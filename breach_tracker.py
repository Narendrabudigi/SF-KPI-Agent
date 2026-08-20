"""
breach_tracker.py — Thread-safe persistent tracker for active KPI breaches.

Manages active_breaches.json to:
1. Normalize KPI shortcut aliases (tth, oct, ott, oft, ear, per) to canonical names.
2. Generate unique composite breach IDs using KPI-aware dimension rules:
   - Employee Attrition Rate → uses Department (one breach per department)
   - All other KPIs (Time-to-Hire, Onboarding, Offboarding, Payroll Error Rate) → uses KPI_ID / process ID
3. Check notification cooldown periods (preventing duplicate alerts).
4. Record notification timestamps.
5. Silently reconcile/purge resolved breaches when they disappear from SAP OData.
"""

import os
import json
import re
import datetime
import threading
from typing import Dict, Any, Tuple, List, Optional
from rich.console import Console

import config

console = Console()
TRACKER_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# KPI Name Normalization & Classification
# ---------------------------------------------------------------------------

def normalize_kpi_name(kpi_name: str) -> str:
    """
    Normalizes any KPI name variation or shortcut alias (tth, oct, ott, oft, ear, per)
    to its standard canonical KPI name.
    """
    if not kpi_name:
        return ""
    kpi_clean = str(kpi_name).strip()
    kpi_lower = kpi_clean.lower()

    if "time to hire" in kpi_lower or "time-to-hire" in kpi_lower or kpi_lower in ("tth", "time_to_hire"):
        return "Time-to-Hire"
    elif "onboarding" in kpi_lower or kpi_lower in ("oct", "ott", "onboarding_completion_time"):
        return "Onboarding Completion Time"
    elif "offboarding" in kpi_lower or kpi_lower in ("oft", "offboarding_completion_time"):
        return "Offboarding Completion Time"
    elif "attrition" in kpi_lower or kpi_lower in ("ear", "employee_attrition_rate"):
        return "Employee Attrition Rate"
    elif "payroll" in kpi_lower or kpi_lower in ("per", "payroll_error_rate"):
        return "Payroll Error Rate"

    return kpi_clean


def _is_attrition_kpi(kpi_name: str) -> bool:
    """
    Returns True ONLY for Employee Attrition Rate.
    This is the only KPI that is department-based (one breach per dept).
    All other KPIs (Time-to-Hire, Onboarding, Offboarding, Payroll Error Rate)
    are individual/incident-based (one breach per KPI_ID / process ID).
    """
    canonical = normalize_kpi_name(kpi_name)
    return canonical == "Employee Attrition Rate"


# ---------------------------------------------------------------------------
# Field Extractors
# ---------------------------------------------------------------------------

def _extract_department(row: Dict[str, Any]) -> Optional[str]:
    """Extracts the department/division value from a row dict (for attrition KPIs)."""
    dept = (
        row.get("DEPARTMENT_ID") or
        row.get("Department_ID") or
        row.get("department_id") or
        row.get("DEPT_ID") or
        row.get("Dept_ID") or
        row.get("Department") or
        row.get("DEPARTMENT") or
        row.get("Dept") or
        row.get("Dept_Name") or
        row.get("DEPT_NAME") or
        row.get("Department_Name") or
        row.get("DEPARTMENT_NAME") or
        row.get("Division") or
        row.get("DIVISION") or
        row.get("CostCenter") or
        row.get("COST_CENTER")
    )
    if dept and str(dept).strip().upper() not in ("NULL", "NONE", ""):
        return str(dept).strip()
    return None


def _extract_kpi_id(row: Dict[str, Any]) -> Optional[str]:
    """
    Extracts the KPI ID / Incident ID from an OData row.
    Supports multiple field name conventions used across different SAP SF modules.
    """
    kpi_id = (
        row.get("KPI_ID") or
        row.get("KPI_Id") or
        row.get("kpi_id") or
        row.get("Kpi_Id") or
        row.get("KPI_ID_Col") or
        row.get("ID") or
        row.get("Id") or
        row.get("KPI_CODE") or
        row.get("KPI_Code") or
        row.get("INCIDENT_ID") or
        row.get("Incident_ID") or
        row.get("BREACH_ID") or
        row.get("Breach_ID") or
        row.get("JOB_REQ_ID") or
        row.get("Job_Req_ID") or
        row.get("ONBOARDING_PROCESS_ID") or
        row.get("Onboarding_Process_ID") or
        row.get("OFFBOARDING_PROCESS_ID") or
        row.get("Offboarding_Process_ID")
    )
    if kpi_id and str(kpi_id).strip().upper() not in ("NULL", "NONE", ""):
        return str(kpi_id).strip()
    return None


# ---------------------------------------------------------------------------
# Breach ID Generation
# ---------------------------------------------------------------------------

def generate_breach_id(kpi_name: str, row: Optional[Dict[str, Any]] = None) -> str:
    """
    Generates a unique, normalized breach ID using KPI-aware dimension rules.

    Rules:
    - Normalizes alias names (TTH, OCT, OTT, OFT, EAR, PER) to canonical names first.
    - Employee Attrition Rate ONLY → uses Department as unique dimension
        e.g. "employee_attrition_rate_sales"

    - All other KPIs (Time-to-Hire, Onboarding, Offboarding, Payroll Error Rate)
      → uses KPI_ID / process ID as unique dimension
        e.g. "time_to_hire_5015885"
             "onboarding_completion_time_onb_992"

    - Fallback (no dimension found) → kpi_name only (slugified)
        e.g. "time_to_hire"
    """
    canonical_kpi = normalize_kpi_name(kpi_name)
    clean_kpi = re.sub(r'[^a-zA-Z0-9]+', '_', canonical_kpi.strip()).strip('_').lower()
    parts = [clean_kpi]

    if row and isinstance(row, dict):
        if _is_attrition_kpi(canonical_kpi):
            # Employee Attrition Rate: use Department as unique dimension
            dept = _extract_department(row)
            if dept:
                clean_dept = re.sub(r'[^a-zA-Z0-9]+', '_', dept).strip('_').lower()
                parts.append(clean_dept)
                console.print(f"[dim][BreachTracker] Attrition KPI ({kpi_name}) → using Department: '{dept}'[/dim]")
            else:
                console.print(f"[yellow][BreachTracker] Attrition KPI ({kpi_name}) but no Department found in row. Using kpi_name only.[/yellow]")
        else:
            # All other KPIs: use KPI_ID / process ID as unique dimension
            kpi_id = _extract_kpi_id(row)
            if kpi_id:
                clean_id = re.sub(r'[^a-zA-Z0-9]+', '_', kpi_id).strip('_').lower()
                parts.append(clean_id)
                console.print(f"[dim][BreachTracker] {canonical_kpi} ({kpi_name}) → using KPI_ID: '{kpi_id}'[/dim]")
            else:
                console.print(f"[yellow][BreachTracker] {canonical_kpi} ({kpi_name}): No KPI_ID found in row. Using kpi_name only.[/yellow]")

    breach_id = "_".join(parts)
    console.print(f"[dim][BreachTracker] Generated breach_id: '{breach_id}'[/dim]")
    return breach_id


# ---------------------------------------------------------------------------
# JSON Persistence
# ---------------------------------------------------------------------------

def _load_active_breaches() -> Dict[str, Any]:
    """Reads active_breaches.json from disk."""
    if config.ACTIVE_BREACHES_FILE.exists():
        try:
            with open(config.ACTIVE_BREACHES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            console.print(f"[yellow]⚠️  Warning: Failed to read active_breaches.json ({e}). Starting fresh.[/yellow]")
            return {}
    return {}


def _save_active_breaches(data: Dict[str, Any]) -> None:
    """Writes active_breaches.json to disk safely using an atomic temp-file swap."""
    try:
        temp_file = config.ACTIVE_BREACHES_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        temp_file.replace(config.ACTIVE_BREACHES_FILE)
    except Exception as e:
        console.print(f"[red]❌ Error saving active_breaches.json: {e}[/red]")


# ---------------------------------------------------------------------------
# Cooldown Check
# ---------------------------------------------------------------------------

def is_in_cooldown(breach_id: str) -> Tuple[bool, Optional[str], float]:
    """
    Checks if a breach was previously notified and is still within the cooldown window.

    Returns:
        (in_cooldown: bool, last_notified_timestamp: str, minutes_remaining: float)
    """
    with TRACKER_LOCK:
        breaches = _load_active_breaches()
        if breach_id not in breaches:
            return False, None, 0.0

        record = breaches[breach_id]
        last_notified_str = record.get("last_notified")
        if not last_notified_str:
            return False, None, 0.0

        try:
            last_notified_dt = datetime.datetime.fromisoformat(last_notified_str)
            if last_notified_dt.tzinfo is None:
                last_notified_dt = last_notified_dt.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            elapsed_minutes = (now - last_notified_dt).total_seconds() / 60.0
            cooldown_minutes = float(config.NOTIFICATION_COOLDOWN_MINUTES)

            if elapsed_minutes < cooldown_minutes:
                minutes_remaining = cooldown_minutes - elapsed_minutes
                return True, last_notified_str, round(minutes_remaining, 1)
        except Exception as e:
            console.print(f"[yellow]⚠️  Could not parse last_notified for '{breach_id}': {e}[/yellow]")

        return False, None, 0.0


# ---------------------------------------------------------------------------
# Record Notification
# ---------------------------------------------------------------------------

def record_breach_notification(
    kpi_name: str,
    severity: str,
    row: Optional[Dict[str, Any]] = None,
    breach_id_override: Optional[str] = None
) -> str:
    """
    Inserts or updates a breach record in active_breaches.json when a notification is sent.
    Uses breach_id_override if provided, otherwise generates one from kpi_name + row.
    Returns the breach_id.
    """
    canonical_kpi = normalize_kpi_name(kpi_name)
    breach_id = breach_id_override if breach_id_override else generate_breach_id(canonical_kpi, row)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Extract department for display (attrition KPIs only)
    dept = None
    if row and isinstance(row, dict):
        dept = _extract_department(row)

    with TRACKER_LOCK:
        breaches = _load_active_breaches()

        existing = breaches.get(breach_id, {})
        first_detected = existing.get("first_detected", now_iso)
        count = existing.get("notification_count", 0) + 1

        breaches[breach_id] = {
            "breach_id": breach_id,
            "kpi_name": canonical_kpi,
            "severity": severity,
            "department": str(dept) if dept else None,
            "first_detected": first_detected,
            "last_notified": now_iso,
            "notification_count": count,
            "status": "ACTIVE"
        }

        _save_active_breaches(breaches)
        console.print(f"[bold green]📌 [BreachTracker] Recorded: '{breach_id}' (notification #{count})[/bold green]")

    return breach_id


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def reconcile_active_breaches(current_active_ids: set) -> List[str]:
    """
    Compares currently-detected breach IDs from SAP OData against active_breaches.json.
    Any breach ID no longer in current_active_ids is considered RESOLVED and purged from disk.

    Returns:
        List of breach IDs that were purged (resolved).
    """
    purged_ids = []
    with TRACKER_LOCK:
        breaches = _load_active_breaches()
        if not breaches:
            return []

        tracked_ids = list(breaches.keys())

        console.print(f"[dim][BreachTracker] Reconciling: {len(tracked_ids)} tracked vs {len(current_active_ids)} active from OData[/dim]")

        for breach_id in tracked_ids:
            if breach_id not in current_active_ids:
                console.print(f"[dim]  → PURGING '{breach_id}' (resolved — not in OData anymore)[/dim]")
                del breaches[breach_id]
                purged_ids.append(breach_id)
            else:
                console.print(f"[dim]  → KEEPING '{breach_id}' (still active in OData)[/dim]")

        if purged_ids:
            _save_active_breaches(breaches)

    return purged_ids


# ---------------------------------------------------------------------------
# Read All
# ---------------------------------------------------------------------------

def get_all_tracked_breaches() -> Dict[str, Any]:
    """Returns all currently tracked breaches for API/dashboard use."""
    with TRACKER_LOCK:
        return _load_active_breaches()
