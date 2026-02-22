import sqlite3
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path("c:/scout/data/scout.db")
RAW_DB_PATH = Path("c:/scout/data/scout_raw.db")

def _get_conn(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_dbs() -> None:
    """Initialize the main Startups DB and the Raw Inbox DB."""
    # 1. Main Scout DB
    with _get_conn(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS Startups (
                id TEXT PRIMARY KEY,
                startup_name TEXT,
                one_liner TEXT,
                vertical TEXT,
                business_model TEXT,
                geography TEXT,
                stage TEXT,
                team_signals TEXT,
                traction_signals TEXT,
                scout_score INTEGER,
                source TEXT,
                source_url TEXT,
                first_seen TIMESTAMP,
                last_updated TIMESTAMP,
                raw_text TEXT
            );

            CREATE TABLE IF NOT EXISTS Top50Rankings (
                rank INTEGER PRIMARY KEY,
                startup_id TEXT,
                FOREIGN KEY(startup_id) REFERENCES Startups(id) ON DELETE CASCADE
            );
        """)
        
    # 2. Raw DB (All scraped signals before Nemotron filters them)
    with _get_conn(RAW_DB_PATH) as conn_raw:
        conn_raw.executescript("""
            CREATE TABLE IF NOT EXISTS RawSignals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                source_url TEXT UNIQUE,
                post_content TEXT,
                engagement_upvotes INTEGER,
                engagement_comments INTEGER,
                engagement_velocity REAL,
                scraped_at TIMESTAMP,
                processed BOOLEAN DEFAULT 0
            );
        """)

def insert_raw_signal(signal: Dict[str, Any]) -> None:
    """Inserts an unprocessed signal into the Raw database."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn(RAW_DB_PATH) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO RawSignals (
                source, source_url, post_content, 
                engagement_upvotes, engagement_comments, engagement_velocity, 
                scraped_at, processed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal.get('source'),
            signal.get('source_url'),
            signal.get('post_content'),
            signal.get('engagement', {}).get('upvotes', 0),
            signal.get('engagement', {}).get('comments', 0),
            signal.get('engagement', {}).get('velocity', 0.0),
            now,
            0
        ))
        conn.commit()

def mark_raw_processed(source_url: str) -> None:
    """Marks a raw signal as having been processed by Nemotron."""
    with _get_conn(RAW_DB_PATH) as conn:
        conn.execute("UPDATE RawSignals SET processed = 1 WHERE source_url = ?", (source_url,))
        conn.commit()

def upsert_startup(startup: Dict[str, Any]) -> None:
    """Insert or update a startup record."""
    now = datetime.now(timezone.utc).isoformat()
    keys = [
        "id", "startup_name", "one_liner", "vertical", "business_model",
        "geography", "stage", "team_signals", "traction_signals",
        "scout_score", "source", "source_url", "raw_text"
    ]
    
    values = []
    for k in keys:
        values.append(startup.get(k))
        
    with _get_conn(DB_PATH) as conn:
        existing = conn.execute("SELECT first_seen FROM Startups WHERE id = ?", (startup["id"],)).fetchone()
        
        if existing:
            update_sql = """
                UPDATE Startups SET 
                startup_name=?, one_liner=?, vertical=?, business_model=?, 
                geography=?, stage=?, team_signals=?, traction_signals=?, 
                scout_score=MAX(scout_score, ?), 
                source=source || ',' || ?, 
                source_url=source_url || ',' || ?, 
                last_updated=?, raw_text=?
                WHERE id=?
            """
            conn.execute(update_sql, (
                startup.get('startup_name'), startup.get('one_liner'), startup.get('vertical'), 
                startup.get('business_model'), startup.get('geography'), startup.get('stage'), 
                startup.get('team_signals'), startup.get('traction_signals'), 
                startup.get('scout_score', 0), startup.get('source', ''), startup.get('source_url', ''), 
                now, startup.get('raw_text', ''), startup['id']
            ))
        else:
            insert_sql = """
                INSERT INTO Startups (
                    id, startup_name, one_liner, vertical, business_model,
                    geography, stage, team_signals, traction_signals,
                    scout_score, source, source_url, raw_text, first_seen, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            values.extend([now, now])
            conn.execute(insert_sql, values)
        
        conn.commit()

def refresh_top50() -> None:
    """Rebuilds the Top50Rankings table based on the highest scout_score."""
    with _get_conn(DB_PATH) as conn:
        conn.execute("DELETE FROM Top50Rankings")
        
        top_startups = conn.execute("""
            SELECT id FROM Startups 
            ORDER BY scout_score DESC, first_seen DESC 
            LIMIT 50
        """).fetchall()
        
        for rank, row in enumerate(top_startups, start=1):
            conn.execute(
                "INSERT INTO Top50Rankings (rank, startup_id) VALUES (?, ?)",
                (rank, row["id"])
            )
        conn.commit()

def get_top50() -> List[Dict[str, Any]]:
    """Retrieve the top 50 startups with their details."""
    with _get_conn(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT s.*, t.rank 
            FROM Top50Rankings t
            JOIN Startups s ON t.startup_id = s.id
            ORDER BY t.rank ASC
        """).fetchall()
        return [dict(row) for row in rows]

def get_startup_by_id(startup_id: str) -> Optional[Dict[str, Any]]:
    with _get_conn(DB_PATH) as conn:
        row = conn.execute("SELECT * FROM Startups WHERE id = ?", (startup_id,)).fetchone()
        return dict(row) if row else None

# Auto-initialize DBs on module load
init_dbs()

if __name__ == "__main__":
    print(f"Databases ready at {DB_PATH.parent}")
