import pandas as pd
from databricks import sql
import config

def fetch_results(table_name: str) -> pd.DataFrame:
    """
    Connects to Databricks SQL Warehouse and fetches ALL results
    from the specified table_name (assuming the notebook overwrites it).
    
    Returns:
        A Pandas DataFrame containing the results. 
        Returns an empty DataFrame if no results are found.
    """
    # Clean the host URL by removing the https:// prefix if present
    # databricks-sql-connector expects just the hostname
    server_hostname = config.DATABRICKS_HOST.replace("https://", "").replace("http://", "")
    
    try:
        with sql.connect(
            server_hostname=server_hostname,
            http_path=config.DATABRICKS_SQL_HTTP_PATH,
            access_token=config.DATABRICKS_TOKEN,
        ) as connection:
            with connection.cursor() as cursor:
                # Execute the query to get all results (assuming the notebook overwrites)
                query = f"SELECT * FROM {table_name}"
                cursor.execute(query)
                
                # Fetch all rows
                rows = cursor.fetchall()
                
                # Extract column names from cursor description
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                
                # Convert to Pandas DataFrame
                df = pd.DataFrame(rows, columns=columns)
                return df
                
    except Exception as e:
        raise RuntimeError(f"Failed to fetch results from Databricks SQL: {e}")
