from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any
import uvicorn

from db import get_top50, get_startup_by_id

app = FastAPI(title="Scout API", description="Startup Discovery Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In dev, allow Vite
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

@app.get("/api/top50")
def get_top_50_feed() -> Dict[str, Any]:
    """
    Returns the top 50 startups specifically formatted for the frontend 
    ClusterMap and NodeGraph components.
    """
    startups = get_top50()
    
    # We must format this to match what ClusterMap.jsx expects.
    # ClusterMap looks for: { entity, raw_trend_score, cat, sources: [], mention_count_1h, etc }
    
    formatted_trends = []
    
    for s in startups:
        # sources are stored as comma separated strings in the db
        sources = str(s.get('source', '')).split(',') if s.get('source') else []
        source_urls = str(s.get('source_url', '')).split(',') if s.get('source_url') else []
        
        # Build node graph representation
        node_graph = []
        for i in range(len(sources)):
            if i < len(sources) and sources[i]:
                node_graph.append({
                    "id": f"{s['id']}_node_{i}",
                    "source_id": sources[i].strip(),
                    "url": source_urls[i].strip() if i < len(source_urls) else "",
                    "headline": s.get('one_liner', ''),
                    "interactions": 100, # Mocked for graph sizes, could store real metrics
                    "views": 500,
                    "score": s.get('scout_score', 0)
                })
        
        formatted_trends.append({
            "id": s['id'],
            "entity": s['startup_name'],
            "trend_score": s['scout_score'],
            "raw_trend_score": s['scout_score'],
            "cat": s.get('vertical', 'other').lower().replace(' ', ''),
            "vertical": s.get('vertical', 'Unknown'),
            "one_liner": s.get('one_liner', ''),
            "stage": s.get('stage', 'Unknown'),
            "sources": sources,
            "mention_count_1h": len(sources), # Proxy based on sources detected
            "node_graph": node_graph
        })
        
    return {"entities": formatted_trends}

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
