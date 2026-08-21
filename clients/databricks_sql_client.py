import pandas as pd
from databricks import sql
import config

def fetch_results(table_name: str) -> pd.DataFrame:
    """
    Connects to Databricks SQL Warehouse and fetches ALL results
    from the specified table_name (assuming the notebook overwrites it).
    
    Safely converts date/timestamp fields to STRING in Spark SQL to avoid 
    Python datetime parsing errors (e.g., invalid isoformat string '-0001-11-30').
    """
    server_hostname = config.DATABRICKS_HOST.replace("https://", "").replace("http://", "")
    
    try:
        with sql.connect(
            server_hostname=server_hostname,
            http_path=config.DATABRICKS_SQL_HTTP_PATH,
            access_token=config.DATABRICKS_TOKEN,
        ) as connection:
            with connection.cursor() as cursor:
                # 1. Inspect table columns via DESCRIBE to identify date/timestamp fields
                date_cols = set()
                try:
                    cursor.execute(f"DESCRIBE {table_name}")
                    desc_rows = cursor.fetchall()
                    for r in desc_rows:
                        if len(r) >= 2:
                            c_name, c_type = str(r[0]), str(r[1]).lower()
                            if 'date' in c_type or 'timestamp' in c_type or 'time' in c_type:
                                date_cols.add(c_name)
                except Exception:
                    pass

                # 2. Build SELECT query casting date/timestamp columns to STRING
                cursor.execute(f"SELECT * FROM {table_name} LIMIT 0")
                all_cols = [desc[0] for desc in cursor.description] if cursor.description else []
                
                if all_cols:
                    select_parts = [
                        f"CAST(`{col}` AS STRING) AS `{col}`" if col in date_cols else f"`{col}`"
                        for col in all_cols
                    ]
                    query = f"SELECT {', '.join(select_parts)} FROM {table_name}"
                else:
                    query = f"SELECT * FROM {table_name}"

                cursor.execute(query)
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                df = pd.DataFrame(rows, columns=columns)
                return df
                
    except Exception as e:
        raise RuntimeError(f"Failed to fetch results from Databricks SQL: {e}")
