import sqlite3
from pathlib import Path

# Path to your local project database
DB_PATH = Path("data/booksql/accounting.sqlite")

def check_database_uniques():
    # 1. Connect to the SQLite database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 2. Query for unique values of AR_paid (from master_txn_table)
    ar_paid_query = """
    SELECT DISTINCT AR_paid 
    FROM master_txn_table 
    ORDER BY AR_paid ASC;
    """
    
    # 3. Query for unique values of Billing_rate (from employees)
    billing_rate_query = """
    SELECT DISTINCT Billing_rate 
    FROM employees 
    WHERE Billing_rate IS NOT NULL
    ORDER BY Billing_rate ASC;
    """
    
    # 4. Query for unique values of Deleted (from employees)
    deleted_query = """
    SELECT DISTINCT Deleted 
    FROM employees 
    ORDER BY Deleted ASC;
    """
    
    try:
        print("=" * 60)
        print("DATABASE UNIQUE VALUE REPORT")
        print("=" * 60)
        
        # --- Execute AR_paid Check ---
        cursor.execute(ar_paid_query)
        ar_paid_values = [str(row[0]) if row[0] is not None else "NULL" for row in cursor.fetchall()]
        print(f"🔹 Unique values for AR_paid (Count: {len(ar_paid_values)}):")
        for val in ar_paid_values:
            print(f"  - '{val}'")
            
        print("-" * 60)
        
        # --- Execute Billing_rate Check ---
        cursor.execute(billing_rate_query)
        billing_rates = [row[0] for row in cursor.fetchall()]
        print(f"🔹 Unique values for Billing_rate (Count: {len(billing_rates)}):")
        if billing_rates:
            print(f"  {billing_rates}")
        else:
            print("  (No numeric billing rates found in this table slice)")
            
        print("-" * 60)
        
        # --- Execute Deleted Check ---
        cursor.execute(deleted_query)
        deleted_values = [str(row[0]) if row[0] is not None else "NULL" for row in cursor.fetchall()]
        print(f"🔹 Unique values for Deleted (Count: {len(deleted_values)}):")
        for val in deleted_values:
            print(f"  - '{val}'")
            
        print("=" * 60)
        
    except sqlite3.OperationalError as e:
        print(f"❌ Database execution error: {e}")
        print("Verify your table names and column structures against the local database schema.")
    finally:
        # 5. Always safely disconnect
        conn.close()

if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"❌ Error: Could not find database file at: {DB_PATH}")
    else:
        check_database_uniques()