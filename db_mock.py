import sqlite3
import os

DB_FILENAME = "mock_database.db"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_FILENAME)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Create a mock tracking table for "Enrollments" / "Customers"
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT,
            last_name TEXT,
            ssn TEXT UNIQUE,
            status TEXT DEFAULT 'Enrolled',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn

def insert_customer(first_name, last_name, ssn):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO customers (first_name, last_name, ssn) VALUES (?, ?, ?)", (first_name, last_name, ssn))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None # SSN already exists
    finally:
        conn.close()

def get_customer_by_ssn(ssn):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM customers WHERE ssn = ?", (ssn,))
    record = cursor.fetchone()
    conn.close()
    if record:
        return {
            "id": record[0],
            "first_name": record[1],
            "last_name": record[2],
            "ssn": record[3],
            "status": record[4],
            "created_at": record[5]
        }
    return None

def verify_customer_creation(ssn, expected_name=None):
    """
    Called by the Verification Agent to confirm the web-action produced a DB row.
    """
    record = get_customer_by_ssn(ssn)
    if not record:
        return False, "Not found in DB", None
    
    # Optional checking logic
    if expected_name and expected_name.lower() not in (record['first_name'] + " " + record['last_name']).lower():
       return False, f"Name mismatch. Expected {expected_name}, got {record['first_name']} {record['last_name']}", record 
    
    return True, "Match confirmed", record

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
