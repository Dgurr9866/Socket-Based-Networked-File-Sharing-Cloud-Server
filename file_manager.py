import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "files.db"


def initialize_database():
    with sqlite3.connect(DATABASE) as connection:
        connection.execute("""
        CREATE TABLE IF NOT EXISTS files (
            file_id TEXT PRIMARY KEY,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            category TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            owner_id TEXT NOT NULL,
            upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.commit()

def save_file_record(file_id, original_name, stored_name, category, file_size, owner_id):

    with sqlite3.connect(DATABASE) as connection:
        connection.execute("""
            INSERT INTO files
            (file_id, original_name, stored_name,
            category, file_size, owner_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            file_id,
            original_name,
            stored_name,
            category,
            file_size,
            owner_id
        ))

        connection.commit()

def get_file_record(file_id):
    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row

        cursor = connection.execute("""
            SELECT *
            FROM files
            WHERE file_id = ?
        """, (file_id,))

        record = cursor.fetchone()

        if record is None:
            return None

        return dict(record)
if __name__ == "__main__":
    initialize_database()
    print("File metadata database initialized successfully")
    file_id = input("Enter a file ID: ")
    record = get_file_record(file_id)

    if record:
        print(record)
    else:
        print("file not found")


