#!/usr/bin/env python3
"""
Nemotron Reclassification Script

Finds all startups in scout.db with Unknown/Unspecified verticals
and uses Nemotron (via OpenRouter) to properly classify them.

Usage:
    python backend/reclassify.py
"""

import sqlite3
import json
import os
import urllib.request
import time
from pathlib import Path

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "scout.db"

CLASSIFY_PROMPT = """You are an expert startup analyst. Given a startup's name and any available context (one-liner, raw text from the source), classify it into exactly ONE of these verticals:

- AI/ML
- Fintech
- Health Tech  
- Climate Tech
- B2B SaaS
- Consumer
- Dev Tools
- Cybersecurity
- Edtech
- Robotics/Hardware
- Other

Also provide a clean one-liner description if the current one is missing or unclear.

Return ONLY a JSON object with these fields:
{
    "vertical": "one of the verticals above",
    "one_liner": "what the startup does in one sentence",
    "business_model": "one of [B2B, B2C, B2B2C, Marketplace, Unclear]",
    "stage": "one of [Pre-revenue, Early revenue, Growth, Unclear]"
}

ABSOLUTELY NO CONVERSATIONAL TEXT. ONLY THE JSON OBJECT."""


def classify_startup(name: str, one_liner: str, raw_text: str) -> dict | None:
    """Use Nemotron to classify a single startup."""
    if not OPENROUTER_API_KEY:
        print("ERROR: No OPENROUTER_API_KEY")
        return None

    context = f"Startup: {name}\n"
    if one_liner:
        context += f"One-liner: {one_liner}\n"
    if raw_text:
        context += f"Source text: {raw_text[:500]}\n"

    payload = {
        "model": "nvidia/llama-3.1-nemotron-70b-instruct",
        "temperature": 0.1,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": CLASSIFY_PROMPT.strip()},
            {"role": "user", "content": context.strip()},
        ],
    }

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://scout-local.dev",
            "X-Title": "Scout Reclassifier",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            content = raw["choices"][0]["message"]["content"].strip()
            # Clean markdown
            if content.startswith("```"):
                content = content.split("\n", 1)[-1]
            if content.endswith("```"):
                content = content[:-3]
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                content = content[start : end + 1]
            return json.loads(content)
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return None


def main():
    db = sqlite3.connect(str(DB_PATH), timeout=10)
    db.row_factory = sqlite3.Row

    # Find unclassified startups
    unclassified = [
        dict(r)
        for r in db.execute(
            """SELECT * FROM Startups 
               WHERE vertical IS NULL 
                  OR vertical = '' 
                  OR LOWER(vertical) IN ('unknown', 'unspecified', 'other')
               ORDER BY scout_score DESC"""
        ).fetchall()
    ]
    
    total = db.execute("SELECT COUNT(*) FROM Startups").fetchone()[0]
    print(f"Found {len(unclassified)} unclassified out of {total} total startups")
    
    if not unclassified:
        print("All startups are classified!")
        db.close()
        return

    classified_count = 0
    for i, row in enumerate(unclassified):
        name = row.get("startup_name", "Unknown")
        # Skip truly junk entries
        if name.lower() in ("unknown", "unspecified", "n/a", "none", ""):
            continue
            
        print(f"[{i+1}/{len(unclassified)}] Classifying: {name} (score: {row.get('scout_score', 0)})")
        
        result = classify_startup(
            name=name,
            one_liner=row.get("one_liner", "") or "",
            raw_text=row.get("raw_text", "") or "",
        )
        
        if result:
            vertical = result.get("vertical", "Other")
            one_liner = result.get("one_liner", "")
            biz_model = result.get("business_model", "Unclear")
            stage = result.get("stage", "Unclear")
            
            print(f"  → {vertical} | {biz_model} | {stage}")
            if one_liner:
                print(f"  → {one_liner[:80]}")
            
            # Update the database
            updates = {"vertical": vertical}
            if biz_model and biz_model != "Unclear":
                updates["business_model"] = biz_model
            if stage and stage != "Unclear":
                updates["stage"] = stage
            # Only update one_liner if currently empty
            if one_liner and not row.get("one_liner"):
                updates["one_liner"] = one_liner
            
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [row["id"]]
            db.execute(f"UPDATE Startups SET {set_clause} WHERE id = ?", values)
            db.commit()
            classified_count += 1
        else:
            print(f"  → FAILED to classify")
        
        # Rate limiting
        time.sleep(0.5)

    db.close()
    print(f"\nDone! Classified {classified_count} startups.")


if __name__ == "__main__":
    main()
