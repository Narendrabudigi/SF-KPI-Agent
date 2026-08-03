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

@app.get("/Yash.png")
def get_yash_logo():
    logo_path = os.path.join(BASE_DIR, "Yash.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path)
    return JSONResponse(content={"error": "Logo not found"}, status_code=404)

@app.get("/api/results")
def get_results():
    """Returns the latest historical AI insights for the dashboard."""
    file_path = "dashboard_data.json"
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
                return JSONResponse(content=data)
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)
    return JSONResponse(content=[])

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