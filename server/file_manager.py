import sqlite3
from pathlib import Path
import uuid
import threading

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
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users(
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash BLOB NOT NULL,
                salt BLOB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                folder_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                folder_name TEXT NOT NULL,
                parent_folder_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                
                FOREIGN KEY (owner_id)
                    REFERENCES users(user_id),
                    
                FOREIGN KEY (parent_folder_id)
                    REFERENCES folders(folder_id),
                    
                UNIQUE(owner_id, parent_folder_id, folder_name)
                )
            """)

        #check whether the files table already has a folder_id column.
        columns = connection.execute(
            "PRAGMA table_info(files)"
        ).fetchall()

        column_names = [column[1] for column in columns]

        if "folder_id" not in column_names:
            connection.execute("""
                ALTER TABLE files
                ADD COLUMN folder_id TEXT
            """)
        connection.commit()

def save_file_record(file_id, original_name, stored_name,
                     category, file_size, owner_id,
                     folder_id=None):

    with sqlite3.connect(DATABASE) as connection:
        connection.execute("""
            INSERT INTO files
            (file_id, original_name, stored_name,
            category, file_size, owner_id, folder_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            file_id,
            original_name,
            stored_name,
            category,
            file_size,
            owner_id,
            folder_id
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

def find_existing_file(owner_id, filename, folder_id=None):
    #find a file with the same name belonging to the same user in the same logical folder

    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row

        record = connection.execute("""
            SELECT
                file_id,
                original_name,
                stored_name,
                category,
                file_size,
                folder_id
            FROM files
            WHERE owner_id = ?
                AND original_name = ?
                AND folder_id IS ?
            LIMIT 1
        """, (
            owner_id,
            filename,
            folder_id
        )).fetchone()

        if record is None:
            return None
        return dict(record)

def replace_file_record(owner_id, file_id, stored_name,
                        category, file_size):
    #update an existing file record after the replacement has been completely updated
    #returns the old storage information so the server can remove the old physical file afterward
    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row

        old_record = connection.execute("""
            SELECT stored_name, category
            FROM files
            WHERE file_id = ? AND owner_id = ?
        """, (file_id, owner_id)).fetchone()

        if old_record is None:
            raise ValueError("Original file no longer exists")

        connection.execute("""
            UPDATE files
            SET stored_name = ?,
                category = ?,
                file_size = ?,
                upload_time = CURRENT_TIMESTAMP
            WHERE file_id = ? AND owner_id = ?
        """, (
            stored_name,
            category,
            file_size,
            file_id,
            owner_id
        ))

        return dict(old_record)

def validate_folder(owner_id, folder_id):
    #None represents the user's root directory.
    if folder_id is None:
        return True

    if not isinstance(folder_id, str):
        return False

    with sqlite3.connect(DATABASE) as connection:
        folder = connection.execute("""
            SELECT folder_id
            FROM folders
            WHERE folder_id = ?
                AND owner_id = ?
        """, (folder_id, owner_id)).fetchone()

    return folder is not None


def list_files(owner_id):
    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row
        #retrieves metadata for files belonging to one user, sorted by upload time, newest first
        cursor = connection.execute("""
            SELECT   
                file_id,
                original_name,
                category,
                file_size,
                upload_time
            FROM files
            WHERE owner_id = ?
            ORDER BY upload_time DESC
        """, (owner_id,))

        records = cursor.fetchall() #use fetchall because possibly retrieving multiple files

        return [dict(record) for record in records] #converts the SQL lite records into a list of python dictionaries


def list_directory(owner_id, folder_id=None):
    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row

        #verify the directory belongs to the user
        if folder_id is not None:
            if not isinstance(folder_id, str):
                return {
                    "status": "error",
                    "message": "Invalid folder ID"
                }

            folder = connection.execute("""
                SELECT folder_id
                FROM folders
                WHERE folder_id = ?
                    AND owner_id = ?
            """, (folder_id, owner_id)).fetchone()

            if folder is None:
                return {
                    "status": "error",
                    "message": "Directory not found"
                }
        #retrieve immediate subfolders
        folders = connection.execute("""
            SELECT folder_id, folder_name, created_at
            FROM folders
            WHERE owner_id = ?
                AND parent_folder_id IS ?
            ORDER BY folder_name
        """, (owner_id, folder_id)).fetchall()

        #retrieve files in this directory
        files = connection.execute("""
            SELECT file_id, original_name, 
                    category, file_size, upload_time
            FROM files
            WHERE owner_id = ?
                AND folder_id IS ?
            ORDER BY original_name
        """, (owner_id, folder_id)).fetchall()

        return {
            "status": "success",
            "folders": [dict(row) for row in folders],
            "files": [dict(row) for row in files]
        }



def create_folder(owner_id, folder_name, parent_folder_id=None):
    #validate the folder name
    if not isinstance(folder_name, str):
        return {
            "status": "error",
            "message": "Invalid folder name"
        }
    if (
        not folder_name
        or folder_name in (".", "..")
        or "/" in folder_name
        or "\\" in folder_name
        or "\x00" in folder_name
    ):
        return {
            "status": "error",
            "message": "Invalid folder name"
        }

    folder_id = uuid.uuid4().hex

    with sqlite3.connect(DATABASE) as connection:
        connection.execute("BEGIN IMMEDIATE")

        if parent_folder_id is not None:
            parent = connection.execute("""
                SELECT folder_id
                FROM folders
                WHERE folder_id = ?
                    AND owner_id = ?
            """, (parent_folder_id, owner_id)).fetchone()

            if parent is None:
                return {
                    "status": "error",
                    "message": "Parent folder not found"
                }

        #check whether the folder already exists.
        existing = connection.execute("""
            SELECT folder_id
            FROM folders
            WHERE owner_id = ?
                AND parent_folder_id IS ?
                AND folder_name = ?
        """, (owner_id, parent_folder_id, folder_name)).fetchone()

        if existing:
            return {
                "status": "error",
                "message": "Folder already exists"
            }
        connection.execute("""
            INSERT INTO folders
            (folder_id, owner_id, folder_name, parent_folder_id)
            VALUES (?, ?, ?, ?)
        """, (
                folder_id,
                owner_id,
                folder_name,
                parent_folder_id
        ))

    return {
        "status": "success",
        "message": "Folder created successfully",
        "folder_id": folder_id
    }


def delete_folder(owner_id, folder_id):
    if not isinstance(folder_id, str) or not folder_id:
        return {
            "status": "error",
            "message": "Invalid folder ID"
        }

    with sqlite3.connect(DATABASE) as connection:
        connection.execute("BEGIN IMMEDIATE")

        #verify that the folder belongs to this user
        folder = connection.execute("""
            SELECT folder_id
            FROM folders
            WHERE folder_id = ?
                AND owner_id = ?
        """, (folder_id, owner_id)).fetchone()

        if folder is None:
            return {
                "status": "error",
                "message": "Folder not found"
            }

        #prevent deletion if the folder contains subfolders
        child = connection.execute("""
            SELECT folder_id
            FROM folders
            WHERE parent_folder_id = ?
                AND owner_id = ?
            LIMIT 1
        """, (folder_id, owner_id)).fetchone()

        if child is not None:
            return {
                "status": "error",
                "message": "Directory is not empty"
            }
        #check whether the folder contains uploaded files
        file_record = connection.execute("""
            SELECT file_id
            FROM files
            WHERE folder_id = ?
                AND owner_id = ?
            LIMIT 1
        """, (folder_id, owner_id)).fetchone()

        if file_record is not None:
            return {
                "status": "error",
                "message": "Directory contains files"
            }


        #delete the empty folder

        connection.execute("""
            DELETE FROM folders
            WHERE folder_id = ?
                AND owner_id = ?
        """, (folder_id, owner_id))

    return {
        "status": "success",
        "message": "Folder deleted successfully"
    }

active_files = set()
active_files_lock = threading.Lock()

#track filenames currently being uploaded or overwritten
active_uploads = set()
active_uploads_lock = threading.Lock()

def acquire_upload(owner_id, folder_id, filename):
    key = (owner_id, folder_id, filename)

    with active_uploads_lock:
        if key in active_uploads:
            return False

        active_uploads.add(key)
        return True

def release_upload(owner_id, folder_id, filename):
    key = (owner_id, folder_id, filename)

    with active_uploads_lock:
        active_uploads.discard(key)

def acquire_file(file_id):
    with active_files_lock:
        if file_id in active_files:
            return False

        active_files.add(file_id)
        return True

def release_file(file_id):
    with active_files_lock:
        active_files.discard(file_id)

def delete_file(owner_id, file_id):
    if not isinstance(file_id, str) or not file_id:
        return {
            "status": "error",
            "message": "Invalid file ID"
        }

    #protect the file from downloads and overwritten
    if not acquire_file(file_id):
        return {
            "status": "error",
            "message": "File is currently being processed"
        }
    upload_locked = False

    try:
        with sqlite3.connect(DATABASE) as connection:
            connection.row_factory = sqlite3.Row

            record = connection.execute("""
                SELECT original_name, folder_id,
                        stored_name, category
                FROM files
                WHERE file_id = ?
                    AND owner_id = ?
            """, (file_id, owner_id)).fetchone()

        if record is None:
            return {
                "status": "error",
                "message": "File not found or access denied"
            }

        filename = record["original_name"]
        folder_id = record["folder_id"]

        #prevent deletion during an upload to this filename
        if not acquire_upload(owner_id, folder_id, filename):
            return {
                "status": "error",
                "message": "An upload with this filename is in progress"
            }
        upload_locked = True

        #recheck the record while holding both locks
        with sqlite3.connect(DATABASE) as connection:
            connection.row_factory = sqlite3.Row

            current_record = connection.execute("""
                SELECT original_name, folder_id,
                        stored_name, category
                FROM files
                WHERE file_id = ?
                    AND owner_id = ?
            """, (file_id, owner_id)).fetchone()

        if current_record is None:
            return {
                "status": "error",
                "message": "File no longer exists"
            }

        if dict(current_record) != dict(record):
            return {
                "status": "error",
                "message": "File changed; retry deletion"
            }
        filepath = (
            BASE_DIR
            / "storage"
            / record["category"]
            / record["stored_name"]
        )

        if not filepath.is_file():
            return {
                "status": "error",
                "message": "File missing from storage"
            }

        #remove the physical file
        filepath.unlink()

        #remove the database record
        with sqlite3.connect(DATABASE) as connection:
            connection.execute("""
                DELETE FROM files
                WHERE file_id = ?
                    AND owner_id = ?
            """, (file_id, owner_id))

        return {
            "status": "success",
            "message": "File deleted successfully"
        }

    finally:
        if upload_locked:
            release_upload(owner_id, folder_id, filename)

        release_file(file_id)


if __name__ == "__main__":

    if __name__ == "__main__":
        initialize_database()

        with sqlite3.connect(DATABASE) as connection:
            user = connection.execute("""
                SELECT user_id
                FROM users
                WHERE username = ?
            """, ("testuser1",)).fetchone()

            if user:
                user_id = user[0]

                parent = connection.execute("""
                    SELECT folder_id
                    FROM folders
                    WHERE owner_id = ?
                      AND folder_name = ?
                      AND parent_folder_id IS NULL
                """, (user_id, "Documents")).fetchone()

        if user and parent:
            result = create_folder(
                owner_id=user_id,
                folder_name="Networks",
                parent_folder_id=parent[0]
            )

            print(result)
        else:
            print("User or Documents folder not found")

