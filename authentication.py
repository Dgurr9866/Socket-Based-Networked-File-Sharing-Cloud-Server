import hashlib
import hmac
import os
import sqlite3
import uuid

from file_manager import DATABASE

ITERATIONS = 600_000


def authenticate_user(username, password):
    if not isinstance(username, str) or not isinstance(password, str):
        return None

    with sqlite3.connect(DATABASE) as connection:
        cursor = connection.execute("""
            SELECT user_id, password_hash, salt
            FROM users
            WHERE username = ?
        """, (username,))

        user = cursor.fetchone()

    if user is None:
        return None

    user_id, stored_hash, salt = user

    provided_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        ITERATIONS
    )

    if hmac.compare_digest(provided_hash, stored_hash):
        return user_id

    return None

def register_user(username, password):
    if not isinstance(username, str) or not 3 <= len(username) <= 32:
        return {
            "status": "error",
            "message": "Password must be at least 12 characters long."
        }

    user_id = uuid.uuid4().hex
    salt = os.urandom(16)

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        ITERATIONS
    )

    try:
        with sqlite3.connect(DATABASE) as connection:
            connection.execute("""
                INSERT INTO users
                (user_id, username, password_hash, salt)
                VALUES (?, ?, ?, ?)
            """, (
                user_id,
                username,
                password_hash,
                salt
            ))
    except sqlite3.IntegrityError:
        return {
            "status": "error",
            "message": "Username already exists."
        }
    return {
        "status": "success",
        "message": "User registered successfully",
        "user_id": user_id
    }

#temporary test registration locally

if __name__ == "__main__":
    from file_manager import initialize_database

    initialize_database()

    print("Correct password:")
    print(authenticate_user("testuser1", "TestingPassword123!"))

    print("Incorrect password:")
    print(authenticate_user("testuser1", "WrongPassword123!"))

    print("Nonexistent user:")
    print(authenticate_user("unknownuser", "TestingPassword123!"))
