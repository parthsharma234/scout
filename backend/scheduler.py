import schedule
import time
import logging
import threading
from scraper import run_pipeline
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("c:/scout/data/scheduler.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def job():
    logger.info("Scheduler: Starting morning scraper run...")
    try:
        run_pipeline()
        logger.info("Scheduler: Morning scraper run completed successfully.")
    except Exception as e:
        logger.error(f"Scheduler Error during run_pipeline: {e}")

# Run every day at 6:00 AM
schedule.every().day.at("06:00").do(job)

def start_scheduler():
    logger.info("Scout Scheduler started. Waiting for 06:00 AM daily...")
    # Optional: Run once instantly on startup if desired, uncomment below:
    # job()
    
    while True:
        schedule.run_pending()
        time.sleep(60) # check every minute

if __name__ == "__main__":
    start_scheduler()
