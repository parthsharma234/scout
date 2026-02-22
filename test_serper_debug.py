import sys
sys.path.insert(0, 'backend')
from config import load_env_file, get_google_serper_key
from enrich_links import serper_search

load_env_file()
key = get_google_serper_key()
print(f"Serper key loaded: {bool(key)}")

results = serper_search("Anthropic startup", limit=5)
print(f"Serper returned {len(results)} results")
for r in results[:3]:
    print(f"  Title: {r.get('title','')[:60]}")
    print(f"  URL:   {r.get('url','')[:80]}")
    print()
