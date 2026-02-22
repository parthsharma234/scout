import time
import logging
from typing import Dict, Any

from db import _get_conn, RAW_DB_PATH, DB_PATH, upsert_startup, mark_raw_processed, refresh_top50
from nemotron import evaluate_post

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def process_unprocessed_raw_signals():
    logger.info("Starting processing of raw signals directly from DB...")
    
    with _get_conn(RAW_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM RawSignals WHERE processed = 0 ORDER BY scraped_at DESC").fetchall()
    
    if not rows:
        logger.info("No unprocessed signals found in scout_raw.db.")
        return
        
    logger.info(f"Found {len(rows)} unprocessed signals. Processing...")
    
    for i, row in enumerate(rows):
        if i % 10 == 0:
            logger.info(f"Evaluating Nemotron Target {i}/{len(rows)}...")
            
        post_content = row['post_content']
        source = row['source']
        source_url = row['source_url']
        engagement = {
            "upvotes": row['engagement_upvotes'],
            "comments": row['engagement_comments'],
            "hours_since_posted": 1, # default fallback
            "velocity": row['engagement_velocity']
        }
        
        result = evaluate_post(post_content, source, engagement)
        mark_raw_processed(source_url)
        
        if result and result.get('is_startup') is True:
            # Add minor deterministic noise to ensure un-clustered scores just in case LLM continues to cluster
            score = result.get('scout_score', 0)
            if isinstance(score, (int, float)):
                noise = (len(source_url) % 7) - 3  # pseudo-random noise between -3 and +3
                score = min(100, max(1, int(score) + noise))
            else:
                score = 0
                
            logger.info(f"✅ Startup Found: {result.get('startup_name')} (Score: {score})")
            
            startup_name = result.get('startup_name')
            if not startup_name:
                startup_name = 'Unknown'
                
            safe_id = "".join(c for c in str(startup_name).lower() if c.isalnum())
            if not safe_id:
                safe_id = f"startup_{int(time.time())}"
                
            startup_record = {
                "id": safe_id,
                "startup_name": startup_name,
                "one_liner": result.get('one_liner'),
                "vertical": result.get('vertical'),
                "business_model": result.get('business_model'),
                "geography": result.get('geography'),
                "stage": result.get('stage'),
                "team_signals": result.get('team_signals'),
                "traction_signals": result.get('traction_signals'),
                "scout_score": score,
                "source": source,
                "source_url": source_url,
                "raw_text": post_content
            }
            upsert_startup(startup_record)
        else:
            time.sleep(0.3)
            
    logger.info("Refreshing Top 50 Rankings...")
    refresh_top50()
    logger.info("Processing complete.")

if __name__ == "__main__":
    process_unprocessed_raw_signals()
