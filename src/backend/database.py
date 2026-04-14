import sqlite3
import pickle
from auth import hash_password
from contextlib import contextmanager

DATABASE_PATH = "calendar.db"


@contextmanager
def get_db_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize the database with the schema. Drops all tables first to reset on restart."""
    with get_db_connection() as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS conversations;
            DROP TABLE IF EXISTS user_events;
            DROP TABLE IF EXISTS events;
            DROP TABLE IF EXISTS users;

            CREATE TABLE IF NOT EXISTS users (
                username VARCHAR PRIMARY KEY,
                password_hash VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title VARCHAR,
                start_time DATETIME,
                end_time DATETIME,
                notes TEXT,
                recurrence_group VARCHAR,
                embedding BLOB
            );

            CREATE TABLE IF NOT EXISTS user_events (
                username VARCHAR,
                event_id INTEGER,
                PRIMARY KEY (username, event_id),
                FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE,
                FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE ON UPDATE CASCADE
            );

            CREATE TABLE IF NOT EXISTS conversations (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username VARCHAR,
                user_message TEXT,
                agent_message TEXT,
                embedding BLOB,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_events_start_time ON events(start_time);
            CREATE INDEX IF NOT EXISTS idx_events_recurrence_group ON events(recurrence_group);
            CREATE INDEX IF NOT EXISTS idx_conversations_username ON conversations(username);
        """)


# =============================================================================
# User operations
# =============================================================================

def create_user(username: str, password_hash: str) -> bool:
    """Create a new user. Returns True if successful, False if user exists."""
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, password_hash)
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_user(username: str) -> dict | None:
    """Get user by username. Returns None if not found."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT username, password_hash FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        if row:
            return {"username": row["username"], "password_hash": row["password_hash"]}
        return None


def delete_user(username: str) -> bool:
    """Delete a user. Returns True if deleted, False if not found."""
    with get_db_connection() as conn:
        cursor = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        return cursor.rowcount > 0


# =============================================================================
# Event operations
# =============================================================================

def create_event(username: str, title: str, start_time: str, end_time: str,
                 participants: list = None, notes: str = "",
                 recurrence_group: str = None, embedding: bytes = None) -> dict:
    """Create a new event and associate it with the owner and participants."""
    with get_db_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO events (title, start_time, end_time, notes, recurrence_group, embedding)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, start_time, end_time, notes, recurrence_group, embedding)
        )
        event_id = cursor.lastrowid

        # Associate event with owner
        conn.execute(
            "INSERT INTO user_events (username, event_id) VALUES (?, ?)",
            (username, event_id)
        )

        # Add participants (each participant is a username in user_events)
        if participants:
            for participant in participants:
                # Skip if participant is the owner (already added)
                if participant != username:
                    try:
                        conn.execute(
                            "INSERT INTO user_events (username, event_id) VALUES (?, ?)",
                            (participant, event_id)
                        )
                    except sqlite3.IntegrityError:
                        # Participant might not exist as a user, skip
                        pass
                    
        return {
            "event_id": event_id,
            "title": title,
            "start_time": start_time,
            "end_time": end_time,
            "participants": participants or [],
            "notes": notes,
            "recurrence_group": recurrence_group
        }


def get_event(event_id: int) -> dict | None:
    """Get a single event by ID."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()

        if not row:
            return None

        # Get participants from user_events
        participants = [r["username"] for r in conn.execute(
            "SELECT username FROM user_events WHERE event_id = ?",
            (event_id,)
        ).fetchall()]

        return {
            "event_id": row["event_id"],
            "title": row["title"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "participants": participants,
            "notes": row["notes"] or "",
            "recurrence_group": row["recurrence_group"]
        }


def get_user_events(username: str) -> list:
    """Get all events for a user (events they own or participate in)."""
    with get_db_connection() as conn:
        rows = conn.execute(
            """SELECT e.* FROM events e
               JOIN user_events ue ON e.event_id = ue.event_id
               WHERE ue.username = ?
               ORDER BY e.start_time""",
            (username,)
        ).fetchall()

        events = []
        for row in rows:
            # Get all participants from user_events
            participants = [r["username"] for r in conn.execute(
                "SELECT username FROM user_events WHERE event_id = ?",
                (row["event_id"],)
            ).fetchall()]

            events.append({
                "event_id": row["event_id"],
                "title": row["title"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "participants": participants,
                "notes": row["notes"] or "",
                "recurrence_group": row["recurrence_group"]
            })

        return events


def update_event(event_id: int, **updates) -> dict | None:
    """Update an event. Supports: title, start_time, end_time, notes, participants, recurrence_group."""
    with get_db_connection() as conn:
        # Check if event exists
        existing = conn.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()

        if not existing:
            return None

        # Handle participants separately
        participants = updates.pop("participants", None)

        # Build update query for other fields
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
            values = list(updates.values()) + [event_id]
            conn.execute(
                f"UPDATE events SET {set_clause} WHERE event_id = ?",
                values
            )

        # Update participants if provided (replace all user_events for this event)
        if participants is not None:
            conn.execute("DELETE FROM user_events WHERE event_id = ?", (event_id,))
            for participant in participants:
                try:
                    conn.execute(
                        "INSERT INTO user_events (username, event_id) VALUES (?, ?)",
                        (participant, event_id)
                    )
                except sqlite3.IntegrityError:
                    # Participant might not exist as a user, skip
                    pass

    # Read after commit so the new connection sees the updated data
    return get_event(event_id)


def delete_event(event_id: int) -> dict | None:
    """Delete an event. Returns the deleted event or None."""
    event = get_event(event_id)
    if not event:
        return None

    with get_db_connection() as conn:
        conn.execute("DELETE FROM events WHERE event_id = ?", (event_id,))

    return event


def query_events(username: str, start_date: str = None, end_date: str = None,
                 participants: list = None, keyword: str = None) -> list:
    """Query events with optional filters."""
    with get_db_connection() as conn:
        query = """
            SELECT DISTINCT e.* FROM events e
            JOIN user_events ue ON e.event_id = ue.event_id
            WHERE ue.username = ?
        """
        params = [username]

        if start_date:
            if " " in start_date:
                query += " AND e.start_time >= ?"
            else:
                query += " AND DATE(e.start_time) >= DATE(?)"
            params.append(start_date)

        if end_date:
            query += " AND DATE(e.start_time) <= DATE(?)"
            params.append(end_date)

        if keyword:
            query += " AND (LOWER(e.title) LIKE LOWER(?) OR LOWER(e.notes) LIKE LOWER(?))"
            keyword_pattern = f"%{keyword}%"
            params.extend([keyword_pattern, keyword_pattern])

        query += " ORDER BY e.start_time"

        rows = conn.execute(query, params).fetchall()

        events = []
        for row in rows:
            # Get participants from user_events
            event_participants = [r["username"] for r in conn.execute(
                "SELECT username FROM user_events WHERE event_id = ?",
                (row["event_id"],)
            ).fetchall()]

            # Filter by participants if specified
            if participants:
                if not any(p.lower() in [ep.lower() for ep in event_participants] for p in participants):
                    continue

            events.append({
                "event_id": row["event_id"],
                "title": row["title"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "participants": event_participants,
                "notes": row["notes"] or "",
                "recurrence_group": row["recurrence_group"]
            })

        return events


def get_events_by_recurrence_group(username: str, recurrence_group: str) -> list:
    """Get all events in a recurrence group for a user."""
    with get_db_connection() as conn:
        rows = conn.execute(
            """SELECT e.* FROM events e
               JOIN user_events ue ON e.event_id = ue.event_id
               WHERE ue.username = ? AND e.recurrence_group = ?
               ORDER BY e.start_time""",
            (username, recurrence_group)
        ).fetchall()

        events = []
        for row in rows:
            # Get participants from user_events
            participants = [r["username"] for r in conn.execute(
                "SELECT username FROM user_events WHERE event_id = ?",
                (row["event_id"],)
            ).fetchall()]

            events.append({
                "event_id": row["event_id"],
                "title": row["title"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "participants": participants,
                "notes": row["notes"] or "",
                "recurrence_group": row["recurrence_group"]
            })

        return events


def update_event_embedding(event_id: int, embedding_vector) -> bool:
    """Update the embedding for an event."""
    embedding_blob = pickle.dumps(embedding_vector) if embedding_vector is not None else None
    with get_db_connection() as conn:
        cursor = conn.execute(
            "UPDATE events SET embedding = ? WHERE event_id = ?",
            (embedding_blob, event_id)
        )
        return cursor.rowcount > 0


def get_events_with_embeddings(username: str) -> list:
    """Get all events with embeddings for a user (for RAG)."""
    with get_db_connection() as conn:
        rows = conn.execute(
            """SELECT e.event_id, e.title, e.start_time, e.notes, e.embedding
               FROM events e
               JOIN user_events ue ON e.event_id = ue.event_id
               WHERE ue.username = ? AND e.embedding IS NOT NULL""",
            (username,)
        ).fetchall()

        results = []
        for row in rows:
            embedding = pickle.loads(row["embedding"]) if row["embedding"] else None
            results.append({
                "event_id": row["event_id"],
                "title": row["title"],
                "start_time": row["start_time"],
                "notes": row["notes"] or "",
                "embedding": embedding
            })

        return results


# =============================================================================
# Conversation operations
# =============================================================================

def save_conversation_message(username: str,
                              user_message: str, agent_message: str,
                              embedding_vector = None) -> int:
    """Save a conversation exchange."""
    embedding_blob = pickle.dumps(embedding_vector) if embedding_vector is not None else None
    with get_db_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO conversations (username, user_message, agent_message, embedding)
               VALUES (?, ?, ?, ?)""",
            (username, user_message, agent_message, embedding_blob)
        )
        return cursor.lastrowid


def get_conversation_history(username: str) -> list:
    """Get conversation history for a user's conversation."""
    with get_db_connection() as conn:
        rows = conn.execute(
            """SELECT user_message, agent_message, created_at FROM conversations
               WHERE username = ?
               ORDER BY created_at""",
            (username,)
        ).fetchall()

        history = []
        for row in rows:
            history.append({"user": row["user_message"]})
            history.append({"agent": row["agent_message"]})

        return history


def get_conversations_with_embeddings(username: str) -> list:
    """Get all conversations with embeddings for a user (for RAG)."""
    with get_db_connection() as conn:
        rows = conn.execute(
            """SELECT message_id, user_message, embedding FROM conversations
               WHERE username = ? AND embedding IS NOT NULL""",
            (username,)
        ).fetchall()

        results = []
        for row in rows:
            embedding = pickle.loads(row["embedding"]) if row["embedding"] else None
            results.append({
                "message_id": row["message_id"],
                "content": row["user_message"],
                "embedding": embedding
            })

        return results


# =============================================================================
# Sample data population (for testing)
# =============================================================================

def populate_sample_data(username: str):
    """Populate sample events for testing. Only runs if user has no events."""
    existing = get_user_events(username)
    if existing:
        return

    # Create participant users so foreign keys in user_events are satisfied
    test_participants = ["Alice", "Bob", "Charlie", "David", "Manager"]
    for participant in test_participants:
        if participant != username and not get_user(participant):
            password_hash = hash_password("test1234")
            create_user(participant, password_hash)

    # Weekly Team Standup - recurring series
    create_event(username, "Team Standup", "2026-01-28 08:00:00", "2026-01-28 08:30:00",
                 ["Alice", "Bob", "Charlie"],
                 "Discussed blockers on the API integration. Bob needs help with authentication. Action items: Review PR #42, Update documentation for new endpoints.",
                 "standup1")

    create_event(username, "Team Standup", "2026-02-04 08:00:00", "2026-02-04 08:30:00",
                 ["Alice", "Bob", "Charlie"], "", "standup1")

    create_event(username, "Team Standup", "2026-02-11 08:00:00", "2026-02-11 08:30:00",
                 ["Alice", "Bob", "Charlie"], "", "standup1")

    # Weekly Project Review - recurring series
    create_event(username, "Project Review", "2026-01-30 14:00:00", "2026-01-30 15:00:00",
                 ["Alice", "David"],
                 "Sprint velocity was 85%. Need to address tech debt in the payment module. Follow up: Schedule meeting with finance team about Q2 budget.",
                 "review1")

    create_event(username, "Project Review", "2026-02-06 14:00:00", "2026-02-06 15:00:00",
                 ["Alice", "David"], "", "review1")

    # Weekly 1:1 Meeting - recurring series
    create_event(username, "1:1 with Manager", "2026-01-29 11:00:00", "2026-01-29 11:30:00",
                 ["Alice", "Manager"],
                 "Discussed career growth path. Manager suggested taking the tech lead course. Need to prepare presentation for Q1 review. Follow up on promotion timeline.",
                 "one2one1")

    create_event(username, "1:1 with Manager", "2026-02-05 11:00:00", "2026-02-05 11:30:00",
                 ["Alice", "Manager"], "", "one2one1")

    # Deep Work blocks
    create_event(username, "Deep Work", "2026-01-09 14:00:00", "2026-01-09 18:00:00",
                 [], "Focused coding session - completed API refactoring", "deepwork1")

    create_event(username, "Deep Work", "2026-01-16 14:00:00", "2026-01-16 18:00:00",
                 [], "Documentation and code review", "deepwork1")

    create_event(username, "Deep Work", "2026-01-23 14:00:00", "2026-01-23 18:00:00",
                 [], "Sprint planning prep", "deepwork1")

    # Lunch meetings
    create_event(username, "Lunch with Bob", "2026-01-21 12:00:00", "2026-01-21 13:00:00",
                 ["Bob"], "Discussed new project ideas", None)

    create_event(username, "Lunch with Bob", "2026-01-28 15:00:00", "2026-01-28 15:30:00",
                 ["Bob"], "Caught up on sprint progress", None)

    # Morning Planning
    create_event(username, "Morning Planning", "2026-01-19 08:30:00", "2026-01-19 09:00:00",
                 [], "Weekly planning session", "morningplan1")

    create_event(username, "Morning Planning", "2026-01-26 08:30:00", "2026-01-26 09:00:00",
                 [], "Reviewed sprint goals", "morningplan1")

    create_event(username, "Morning Planning", "2026-02-02 08:30:00", "2026-02-02 09:00:00",
                 [], "", "morningplan1")
    
    # Budget Review - recurring series
    create_event(username, "Budget Review", "2026-01-18 14:00:00", "2026-01-18 15:00:00",
                 ["Charlie"], "", "budgetreview1")

    create_event(username, "Budget Review", "2026-01-25 14:00:00", "2026-01-25 15:00:00",
                 ["Charlie"], "", "budgetreview1")

    create_event(username, "Budget Review", "2026-02-01 14:00:00", "2026-02-01 15:00:00",
                 ["Charlie"], "", "budgetreview1")


# Initialize database on module import (resets on every restart)
init_db()

# Create test users with real passwords (password: "test1234")
_test_users = ["Alice", "Bob", "Charlie", "David", "Manager"]
for _user in _test_users:
    _hash = hash_password("test1234")
    create_user(_user, _hash)

# Populate sample events for Alice (the primary test user)
populate_sample_data("Alice")