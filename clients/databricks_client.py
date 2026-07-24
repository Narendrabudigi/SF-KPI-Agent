"""
databricks_client.py — Thin wrapper around the Databricks Jobs REST API (v2.2).

Provides methods to submit notebook runs, check status, fetch output,
and cancel runs. All HTTP errors are raised as DatabricksAPIError.
"""

import requests
from typing import Any, Optional

import config


class DatabricksAPIError(Exception):
    """Raised when the Databricks API returns a non-2xx response."""

    def __init__(self, status_code: int, message: str, response_body: dict | None = None):
        self.status_code = status_code
        self.response_body = response_body or {}
        super().__init__(f"[HTTP {status_code}] {message}")


def _handle_response(response: requests.Response) -> dict:
    """Parse the JSON response; raise DatabricksAPIError on failure."""
    try:
        body = response.json()
    except ValueError:
        body = {}

    if not response.ok:
        error_msg = body.get("message", response.text or "Unknown error")
        raise DatabricksAPIError(response.status_code, error_msg, body)

    return body


# ---------------------------------------------------------------------------
# API Methods
# ---------------------------------------------------------------------------

def submit_run(
    notebook_path: str,
    parameters: Optional[dict[str, str]] = None,
    run_name: Optional[str] = None,
) -> dict[str, Any]:
    """
    Submit a one-time notebook run.

    Args:
        notebook_path: Workspace path to the notebook.
        parameters: Optional dict of base_parameters to pass to the notebook.
        run_name: Optional human-readable name for the run.

    Returns:
        Dict with at least {"run_id": int}.
    """
    payload: dict[str, Any] = {
        "run_name": run_name or f"agent-run-{notebook_path.split('/')[-1]}",
        "tasks": [
            {
                "task_key": "notebook_run",
                "notebook_task": {
                    "notebook_path": notebook_path,
                    "source": "WORKSPACE",
                },
            }
        ],
    }

    # Attach notebook parameters if provided
    if parameters:
        payload["tasks"][0]["notebook_task"]["base_parameters"] = parameters

    # Note: No cluster config — uses serverless compute automatically

    response = requests.post(
        f"{config.API_BASE_URL}/jobs/runs/submit",
        headers=config.AUTH_HEADERS,
        json=payload,
        timeout=30,
    )

    return _handle_response(response)


def get_run_status(run_id: int) -> dict[str, Any]:
    """
    Get the current status of a run.

    Returns:
        Full run metadata including:
        - state.life_cycle_state: PENDING | RUNNING | TERMINATING | TERMINATED | SKIPPED | INTERNAL_ERROR
        - state.result_state: SUCCESS | FAILED | TIMEDOUT | CANCELED | ... (only when terminated)
        - state.state_message: Human-readable message
    """
    response = requests.get(
        f"{config.API_BASE_URL}/jobs/runs/get",
        headers=config.AUTH_HEADERS,
        params={"run_id": run_id},
        timeout=30,
    )

    return _handle_response(response)


def get_task_run_id(parent_run_id: int) -> int:
    """
    For tasks-based (serverless) runs, extract the individual task's run_id.

    The parent run_id can't be used with get-output directly — Databricks
    requires fetching output per individual task.
    """
    run_data = get_run_status(parent_run_id)
    tasks = run_data.get("tasks", [])

    if tasks:
        task_run_id = tasks[0].get("run_id")
        return task_run_id or parent_run_id

    # Fallback: if no tasks array, it's a non-tasks run — use parent run_id
    return parent_run_id


def export_run(run_id: int) -> str:
    """
    Export the full notebook run as HTML.

    This captures all cell outputs (stdout, tables, errors) even if the
    notebook didn't use dbutils.notebook.exit().

    Returns:
        The HTML string of the notebook run, or empty string on failure.
    """
    task_run_id = get_task_run_id(run_id)

    response = requests.get(
        f"{config.API_BASE_URL}/jobs/runs/export",
        headers=config.AUTH_HEADERS,
        params={"run_id": task_run_id, "views_to_export": "ALL"},
        timeout=30,
    )

    body = _handle_response(response)
    views = body.get("views", [])
    if views:
        return views[0].get("content", "")
    return ""


def get_run_output(run_id: int) -> dict[str, Any]:
    """
    Get the output of a completed run.

    For serverless (tasks-based) runs, automatically resolves the
    individual task run_id before fetching output.

    If the notebook used `dbutils.notebook.exit(value)`, the result appears
    in response["notebook_output"]["result"].

    Note: Only the first 5 MB of output is returned. For larger outputs,
    configure the notebook to write results to cloud storage.

    Returns:
        Full output metadata including notebook_output, error, error_trace, etc.
    """
    # Resolve the task-level run_id for serverless runs
    task_run_id = get_task_run_id(run_id)

    response = requests.get(
        f"{config.API_BASE_URL}/jobs/runs/get-output",
        headers=config.AUTH_HEADERS,
        params={"run_id": task_run_id},
        timeout=30,
    )

    return _handle_response(response)


def cancel_run(run_id: int) -> dict[str, Any]:
    """
    Cancel a running or pending run.

    Returns:
        Empty dict on success.
    """
    response = requests.post(
        f"{config.API_BASE_URL}/jobs/runs/cancel",
        headers=config.AUTH_HEADERS,
        json={"run_id": run_id},
        timeout=30,
    )

    return _handle_response(response)


def list_runs(
    limit: int = 10,
    active_only: bool = False,
) -> dict[str, Any]:
    """
    List recent runs (useful for debugging).

    Args:
        limit: Max runs to return.
        active_only: If True, only return active (non-terminated) runs.

    Returns:
        Dict with "runs" list and pagination info.
    """
    params: dict[str, Any] = {"limit": limit}
    if active_only:
        params["active_only"] = "true"

    response = requests.get(
        f"{config.API_BASE_URL}/jobs/runs/list",
        headers=config.AUTH_HEADERS,
        params=params,
        timeout=30,
    )

    return _handle_response(response)
