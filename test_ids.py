import sqlite3
c = sqlite3.connect("c:/scout/data/scout.db")
c.row_factory = sqlite3.Row
# Check what IDs look like 
rows = c.execute("SELECT id, startup_name FROM Startups ORDER BY scout_score DESC LIMIT 5").fetchall()
for r in rows:
    print(f"id={r['id']}  name={r['startup_name']}")

# Try the exact entity_key from the URL
test_key = "worldlabs"
r1 = c.execute("SELECT id, startup_name FROM Startups WHERE id = ?", (test_key,)).fetchone()
print(f"\nBy id '{test_key}': {dict(r1) if r1 else 'NOT FOUND'}")

r2 = c.execute("SELECT id, startup_name FROM Startups WHERE startup_name = ? COLLATE NOCASE", (test_key,)).fetchone()
print(f"By name '{test_key}': {dict(r2) if r2 else 'NOT FOUND'}")

# Check what the actual ID format is
r3 = c.execute("SELECT id, startup_name FROM Startups WHERE startup_name LIKE '%World%'").fetchall()
for r in r3:
    print(f"\nWorld match: id={r['id']}  name={r['startup_name']}")
