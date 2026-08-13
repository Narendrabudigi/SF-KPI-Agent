import threading
from fastapi import FastAPI
from rich.console import Console
from rich.panel import Panel

# Import the orchestrator from the new agents package
from agents.orchestrator import run_orchestrator

console = Console()

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    console.print(Panel("[bold cyan]HR KPI Agentic AI Framework Initializing...[/bold cyan]\nStarting the 24/7 background master service in a thread."))
    
    # Start the master orchestrator loop in a background thread so FastAPI can return
    thread = threading.Thread(target=run_orchestrator, daemon=True)
    thread.start()
    
    yield

app = FastAPI(title="HR KPI Agentic AI API", lifespan=lifespan)

from agents.mcp_server import mcp
app.mount("/mcp", mcp.sse_app())

from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from agents.orchestrator import check_sap_breaches
import os
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/")
def health_check():
    return {"status": "HR KPI Agentic AI Framework is running"}

@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard():
    dashboard_path = os.path.join(BASE_DIR, "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard not found. Run the setup script.</h1>", status_code=404)

@app.get("/timetohire", response_class=HTMLResponse)
def get_timetohire_page():
    file_path = os.path.join(BASE_DIR, "timetohire.html")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Time-to-Hire Page not found.</h1>", status_code=404)

@app.get("/onboarding", response_class=HTMLResponse)
def get_onboarding_page():
    file_path = os.path.join(BASE_DIR, "onboarding.html")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Onboarding Page not found.</h1>", status_code=404)

@app.get("/attrition", response_class=HTMLResponse)
def get_attrition_page():
    file_path = os.path.join(BASE_DIR, "attrition.html")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Attrition Page not found.</h1>", status_code=404)

@app.get("/offboarding", response_class=HTMLResponse)
def get_offboarding_page():
    file_path = os.path.join(BASE_DIR, "offboarding.html")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Offboarding Page not found.</h1>", status_code=404)

@app.get("/alerts", response_class=HTMLResponse)
def get_alerts_page():
    file_path = os.path.join(BASE_DIR, "alerts.html")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Alerts Page not found.</h1>", status_code=404)

@app.get("/Yash.png")
def get_yash_logo():
    logo_path = os.path.join(BASE_DIR, "Yash.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path)
    return JSONResponse(content={"error": "Logo not found"}, status_code=404)

@app.get("/api/results")
def get_results():
    """Returns the latest historical AI insights for the dashboard."""
    file_path = os.path.join(BASE_DIR, "dashboard_data.json")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return JSONResponse(content=data)
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)
    return JSONResponse(content=[])

@app.get("/api/breaches/{kpi_slug}")
def get_breach_by_slug(kpi_slug: str):
    """Returns breach data filtered for a specific KPI slug along with active breach info."""
    slug_map = {
        "timetohire": ["Time-to-Hire", "Time to Hire", "tth"],
        "onboarding": ["Onboarding Completion Time", "Onboarding", "oct"],
        "attrition": ["Employee Attrition Rate", "Attrition", "ear"],
        "offboarding": ["Offboarding Completion Time", "Offboarding", "oft"]
    }
    targets = [t.lower() for t in slug_map.get(kpi_slug.lower(), [kpi_slug.lower()])]
    
    # Load dashboard data
    dash_path = os.path.join(BASE_DIR, "dashboard_data.json")
    matched_entry = None
    if os.path.exists(dash_path):
        try:
            with open(dash_path, "r", encoding="utf-8") as f:
                history = json.load(f)
                for entry in history:
                    kpi_name = entry.get("kpi_name", "").lower()
                    if any(target in kpi_name for target in targets):
                        matched_entry = entry
                        break
        except Exception as e:
            pass
            
    # Load active breaches tracker info
    active_path = os.path.join(BASE_DIR, "active_breaches.json")
    active_info = None
    if os.path.exists(active_path):
        try:
            with open(active_path, "r", encoding="utf-8") as f:
                active_breaches = json.load(f)
                for b_id, b_data in active_breaches.items():
                    kpi_name = b_data.get("kpi_name", "").lower()
                    if any(target in kpi_name for target in targets):
                        active_info = b_data
                        break
        except Exception:
            pass
            
    return JSONResponse(content={"entry": matched_entry, "active_info": active_info})

@app.get("/api/alerts")
def get_all_alerts():
    """Returns summary of all alerts combining historical data & active breach statuses."""
    dash_path = os.path.join(BASE_DIR, "dashboard_data.json")
    active_path = os.path.join(BASE_DIR, "active_breaches.json")
    
    history = []
    if os.path.exists(dash_path):
        try:
            with open(dash_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            pass
            
    active_breaches = {}
    if os.path.exists(active_path):
        try:
            with open(active_path, "r", encoding="utf-8") as f:
                active_breaches = json.load(f)
        except Exception:
            pass
            
    return JSONResponse(content={"alerts": history, "active_breaches": active_breaches})

@app.get("/api/status")
def get_status():
    """Returns the live in-memory execution status of the system."""
    import runtime_state
    return JSONResponse(content=runtime_state.SYSTEM_STATE)

@app.post("/api/reset")
def reset_results():
    """Clears the historical AI insights."""
    file_path = "dashboard_data.json"
    if os.path.exists(file_path):
        try:
            with open(file_path, "w") as f:
                json.dump([], f)
            return {"status": "success", "message": "Dashboard data cleared"}
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)
    return {"status": "success", "message": "No data to clear"}

@app.post("/api/clear_logs")
def clear_logs():
    """Clears the live agent console events/logs in memory."""
    import runtime_state
    runtime_state.SYSTEM_STATE["system_logs"] = []
    return {"status": "success", "message": "Console logs cleared"}

@app.post("/api/trigger")
def trigger_now_post():
    """Endpoint to manually trigger a single SAP check cycle"""
    console.print(Panel("[bold yellow]Manual Trigger Received! Initiating SAP Check...[/bold yellow]"))
    
    try:
        check_sap_breaches()
        return {"status": "Manual check completed! Check Vercel logs for results."}
    except Exception as e:
        console.print(f"[bold red]Error during manual check: {e}[/bold red]")
        return {"status": f"Error occurred: {str(e)}"}

if __name__ == "__main__":
    import uvicorn
    try:
        uvicorn.run(app, host="0.0.0.0", port=8081)
    except KeyboardInterrupt:
        print("\n🛑 Server shutdown requested by user. Exiting gracefully...")