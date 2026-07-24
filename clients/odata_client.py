import json
import requests
from rich.console import Console
from rich.panel import Panel
import config

console = Console()


def _get_oauth_token():
    """
    Gets an OAuth2 access token from SAP Datasphere using Client Credentials flow.
    """
    token_url = config.OAUTH_TOKEN_URL
    client_id = config.OAUTH_CLIENT_ID
    client_secret = config.OAUTH_CLIENT_SECRET
    
    if not token_url or not client_id or not client_secret:
        console.print("[red]Missing OAuth configuration (OAUTH_TOKEN_URL, OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET) in .env[/red]")
        return None
    
    try:
        console.print(f"[cyan]Requesting OAuth2 token from: {token_url}[/cyan]")
        
        response = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
            },
            auth=(client_id, client_secret),
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
            timeout=30
        )
        
        if response.status_code == 200:
            token_data = response.json()
            access_token = token_data.get("access_token")
            console.print("[green]✅ OAuth2 token acquired successfully![/green]")
            return access_token
        else:
            console.print(Panel(
                f"[bold red]OAuth2 Token Request Failed![/bold red]\n"
                f"Status: {response.status_code}\n"
                f"Response: {response.text[:500]}",
                title="OAuth Error"
            ))
            return None
            
    except Exception as e:
        console.print(Panel(f"[bold red]OAuth2 Token Exception[/bold red]\n{e}", title="Error"))
        return None


def fetch_kpi_data():
    """
    Fetches the KPI data from the SAP Datasphere OData service using OAuth2.
    Uses Client Credentials flow — no browser needed, works on Vercel Serverless.
    """
    if not config.ODATA_URL:
        console.print("[red]Missing ODATA_URL in .env[/red]")
        return None

    # Step 1: Get an OAuth2 access token
    access_token = _get_oauth_token()
    if not access_token:
        return None
    
    try:
        # Step 2: Call the OData API with the Bearer token
        odata_url = config.ODATA_URL + "?$format=json"
        console.print(f"[cyan]Fetching OData from: {odata_url}[/cyan]")
        
        response = requests.get(
            odata_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json"
            },
            timeout=60
        )
        
        if response.status_code != 200:
            console.print(Panel(
                f"[bold red]OData API Request Failed![/bold red]\n"
                f"Status: {response.status_code}\n"
                f"Response: {response.text[:500]}",
                title="OData Error"
            ))
            return None
        
        console.print("[green]✅ Successfully fetched OData via OAuth2![/green]")
        
        # Step 3: Parse the JSON response
        data = response.json()
        
        # Parse the SAP OData structure (it stores rows in the 'value' array)
        if "value" in data:
            rows = data["value"]
        elif "d" in data and "results" in data["d"]:
            rows = data["d"]["results"]
        else:
            rows = data
            
        # Clean up the KPI_Name by stripping leading whitespace/tabs
        for row in rows:
            if "KPI_Name" in row and isinstance(row["KPI_Name"], str):
                row["KPI_Name"] = row["KPI_Name"].strip()
            elif "KPI_NAME" in row and isinstance(row["KPI_NAME"], str):
                # Fallback for old schema just in case
                row["KPI_Name"] = row.pop("KPI_NAME").strip()
        
        console.print(f"[green]Fetched {len(rows)} rows from OData.[/green]")
        return rows
        
    except json.JSONDecodeError as json_e:
        console.print(f"[bold red]Failed to parse OData JSON![/bold red] {json_e}")
        return None
    except Exception as e:
        console.print(Panel(f"[bold red]OData Connection Exception[/bold red]\n{e}", title="Error"))
        return None


if __name__ == "__main__":
    # Test block
    data = fetch_kpi_data()
    if data:
        console.print(data)
