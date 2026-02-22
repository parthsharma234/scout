"""
Stub pipeline_runner module.
Provides the symbols that search_api.py expects so the server can boot.
The actual scraping pipeline lives in scraper.py / scheduler.py.
"""

import threading
import time


class PipelineScheduler:
    """Minimal scheduler stub – the real scheduling is in scheduler.py."""

    def __init__(self):
        self.is_running = False
        self._thread = None

    def start(self, interval_hours: float = 1.0):
        self.is_running = True

    def stop(self):
        self.is_running = False


def run_full_pipeline(mode: str = "manual", do_backfill: bool = True, do_enrichment: bool = True) -> dict:
    """Stub – returns a no-op result."""
    return {"ok": True, "mode": mode, "message": "Pipeline stub – use scraper.py directly."}


def run_on_demand_enrichment(entity_keys: list, max_links: int = 8) -> dict:
    """Stub enrichment trigger."""
    return {"ok": True, "links_added": 0, "message": "Enrichment stub."}
