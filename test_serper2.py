"""Quick inline test of serper_search inside the same process context"""
import sys, os
sys.path.insert(0, 'backend')

# Simulate what search_api.py does
from config import load_env_file
load_env_file()

print("GOOGLE_SERPER_KEY in env:", "GOOGLE_SERPER_KEY" in os.environ)
print("Key value:", os.environ.get("GOOGLE_SERPER_KEY", "")[:10] + "...")

from enrich_links import serper_search, _request_json, SERPER_ENDPOINT
import json

# Manual test
query = "World Labs startup"
api_key = os.environ.get("GOOGLE_SERPER_KEY", "")
payload = json.dumps({"q": query, "num": 10}).encode("utf-8")

import urllib.request
req = urllib.request.Request(
    SERPER_ENDPOINT,
    data=payload,
    headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
    method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
        data = json.loads(raw)
        organic = data.get("organic", [])
        print(f"Direct API call returned: {len(organic)} results")
        for r in organic[:3]:
            print(f"  {r.get('title','')[:50]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")

# Now test via serper_search wrapper
results = serper_search(query, limit=10)
print(f"\nserper_search wrapper returned: {len(results)} results")
for r in results[:3]:
    print(f"  {r.get('title','')[:50]}")
