#!/usr/bin/env python3
import os
import sys
from pathlib import Path

# Add backend to path
sys.path.append(str(Path(__file__).parent.parent))

from backend.config import load_env_file, get_google_serper_key
from backend.enrich_links import serper_search, enrich_entity

def test_serper():
    load_env_file()
    key = get_google_serper_key()
    if not key:
        print("❌ GOOGLE_SERPER_KEY not found in .env")
        return

    print(f"✅ Found Serper key (ends in {key[-4:]})")
    
    query = "OpenAI startup"
    print(f"🔍 Testing serper_search for: {query}")
    results = serper_search(query, limit=3)
    
    if results:
        print(f"✅ Found {len(results)} results:")
        for r in results:
            print(f"  - {r['title']} ({r['url']})")
    else:
        print("❌ No results found or API error")

if __name__ == "__main__":
    test_serper()
