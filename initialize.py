import sqlite3
import os

# Constants
DATABASE_NAME = "db.sqlite"
SQL_FILE = "db.sql"

def initialize_database():
    # Check if SQL file exists
    if not os.path.exists(SQL_FILE):
        print(f"Error: '{SQL_FILE}' file not found. Make sure it is in the same directory.")
        return

    try:
        # Use context manager for automatic connection close
        with sqlite3.connect(DATABASE_NAME) as conn:
            cursor = conn.cursor()
            
            # Read SQL file
            with open(SQL_FILE, 'r', encoding='utf-8') as f:
                sql_script = f.read()

            # Execute multiple SQL statements
            cursor.executescript(sql_script)
            conn.commit()

            print(f"Database '{DATABASE_NAME}' created and populated successfully.")

    except sqlite3.OperationalError as e:
        print("SQLite OperationalError:", e)
    except sqlite3.DatabaseError as e:
        print("DatabaseError:", e)
    except Exception as e:
        print("Unexpected error:", e)

if __name__ == "__main__":
    initialize_database()
