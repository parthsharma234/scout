import sqlite3
db = sqlite3.connect("c:/scout/data/scout.db")
total = db.execute("SELECT COUNT(*) FROM Startups").fetchone()[0]
classified = db.execute("SELECT COUNT(*) FROM Startups WHERE vertical IS NOT NULL AND LOWER(vertical) NOT IN ('unknown','unspecified','other','')").fetchone()[0]
junk_names = db.execute("SELECT COUNT(*) FROM Startups WHERE LOWER(startup_name) LIKE '%unknown%' OR LOWER(startup_name) LIKE '%unspecified%'").fetchone()[0]
clean = db.execute("SELECT COUNT(*) FROM Startups WHERE LOWER(startup_name) NOT LIKE '%unknown%' AND LOWER(startup_name) NOT LIKE '%unspecified%' AND LOWER(startup_name) NOT LIKE '%untitled%' AND LENGTH(startup_name) > 2").fetchone()[0]
print(f"Total: {total}")
print(f"Classified verticals: {classified}")
print(f"Junk names: {junk_names}")
print(f"Clean names: {clean}")
# Show top 5 clean names
db.row_factory = sqlite3.Row
rows = db.execute("SELECT startup_name, vertical, scout_score FROM Startups WHERE LOWER(startup_name) NOT LIKE '%unknown%' AND LOWER(startup_name) NOT LIKE '%unspecified%' AND LENGTH(startup_name) > 2 ORDER BY scout_score DESC LIMIT 5").fetchall()
for r in rows:
    print(f"  {r['startup_name'][:30]:30s} v={r['vertical'] or 'NULL':20s} score={r['scout_score']}")
