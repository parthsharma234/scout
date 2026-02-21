# Scout — PulseSignal: Frontend ↔ Backend API Contract

> **For the backend team.** This document defines every endpoint and WebSocket message the React frontend expects from the Django backend. Match these schemas exactly and the frontend will wire up automatically.

---

## Base URLs

| Channel  | URL                          |
|----------|------------------------------|
| REST API | `http://localhost:8000/api/` |
| WebSocket| `ws://localhost:8000/ws/trends/` |

Frontend proxies these via Vite in dev (`vite.config.js`). In production, point `VITE_API_URL` and `VITE_WS_URL` env vars.

---

## REST Endpoints

### `GET /api/trends/`

Returns the current ranked list of tracked entities sorted by `trend_score` descending.

**Response:**
```json
{
  "entities": [
    {
      "entity": "Lumo AI",
      "trend_score": 87.4,
      "mention_count_1h": 42,
      "mention_count_24h": 180,
      "velocity_delta_pct": 34.0,
      "sentiment": {
        "positive": 0.72,
        "neutral": 0.20,
        "negative": 0.08
      },
      "sources": ["hackernews", "reddit"],
      "source_counts": {
        "hackernews": 28,
        "reddit": 14,
        "techcrunch": 0,
        "twitter": 0,
        "producthunt": 0
      },
      "top_keywords": ["AI", "YC", "seed round", "API"],
      "spike_detected": true
    }
  ]
}
```

| Field | Type | Notes |
|-------|------|-------|
| `entity` | string | Canonical entity name |
| `trend_score` | float | 0–100 composite score |
| `mention_count_1h` | int | Mentions in last hour |
| `mention_count_24h` | int | Mentions in last 24h |
| `velocity_delta_pct` | float | % change vs previous window |
| `sentiment.positive/neutral/negative` | float | Must sum to 1.0 |
| `sources` | string[] | Sources where entity appeared |
| `source_counts` | object | Per-source mention count |
| `top_keywords` | string[] | Top associated keywords |
| `spike_detected` | bool | True if velocity > threshold |

---

### `GET /api/velocity/?window=1h`

Returns time-series velocity data for the line chart.

**Query params:** `window` = `1h` | `6h` | `24h`

**Response:**
```json
{
  "entities": ["Lumo AI", "Vanta Labs", "Synthesis AI"],
  "points": [
    { "time": "14:00", "Lumo AI": 12.0, "Vanta Labs": 5.0, "Synthesis AI": 3.5 },
    { "time": "14:05", "Lumo AI": 18.0, "Vanta Labs": 4.5, "Synthesis AI": 6.0 }
  ]
}
```

- `entities` — the entity names as keys in each point (top N, max 10)
- `points` — chronologically ordered, `time` is a display string (e.g. `"14:30"` or `"Mon 09:00"`)
- Each entity key is its mention velocity value (float) at that time interval

---

### `GET /api/sources/`

Returns current health status of each scraper agent.

**Response:**
```json
{
  "sources": [
    {
      "id": "hackernews",
      "status": "live",
      "last_scraped": "2024-01-15T14:32:00Z",
      "next_scrape": "2024-01-15T14:33:00Z",
      "items_ingested": 1247,
      "error_message": null
    },
    {
      "id": "twitter",
      "status": "cached",
      "last_scraped": "2024-01-15T12:00:00Z",
      "next_scrape": null,
      "items_ingested": 348,
      "error_message": null
    }
  ]
}
```

| `id` values | Description |
|-------------|-------------|
| `hackernews` | HN scraper |
| `reddit` | Reddit scraper |
| `techcrunch` | TechCrunch RSS |
| `twitter` | Twitter/X (may be cached) |
| `producthunt` | Product Hunt |

| `status` values | Meaning |
|-----------------|---------|
| `live` | Actively scraping |
| `cached` | Using pre-cached data |
| `rate_limited` | Hit API rate limit, backing off |
| `error` | Scraper failed |

---

## WebSocket — `ws://localhost:8000/ws/trends/`

The frontend connects on dashboard load and listens for pushed messages. All messages use this envelope:

```json
{
  "type": "<message_type>",
  "payload": { ... }
}
```

---

### `trend_update`

Push the full updated entity list. Send this after every NLP pipeline run.

```json
{
  "type": "trend_update",
  "payload": {
    "entities": [ /* same shape as GET /api/trends/ entities array */ ]
  }
}
```

---

### `velocity_update`

Push a single new data point for the velocity chart. Send every polling cycle.

```json
{
  "type": "velocity_update",
  "payload": {
    "point": {
      "time": "14:35",
      "Lumo AI": 22.5,
      "Vanta Labs": 8.0,
      "Synthesis AI": 11.0
    }
  }
}
```

---

### `spike_alert`

Push when an entity crosses the spike detection threshold. The frontend shows this as an alert card.

```json
{
  "type": "spike_alert",
  "payload": {
    "id": "alert-uuid-1234",
    "entity": "Lumo AI",
    "message": "3× normal velocity detected across HN and Reddit",
    "sources": ["hackernews", "reddit"],
    "velocity_multiplier": 3.1,
    "timestamp": "2024-01-15T14:35:00Z"
  }
}
```

---

### `source_status`

Push whenever a source's status changes (live → rate_limited, etc.).

```json
{
  "type": "source_status",
  "payload": {
    "sources": [ /* same shape as GET /api/sources/ sources array */ ]
  }
}
```

---

## Django Channels Setup Notes

The frontend connects to `ws://localhost:8000/ws/trends/`. In Django Channels your routing should look like:

```python
# routing.py
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/trends/$', consumers.TrendsConsumer.as_asgi()),
]
```

```python
# consumers.py (minimal example)
from channels.generic.websocket import AsyncJsonWebsocketConsumer

class TrendsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("trends", self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard("trends", self.channel_name)

    # Called by Celery task / signal after each pipeline run:
    async def trend_update(self, event):
        await self.send_json(event["message"])
```

To push from a Celery task:
```python
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

channel_layer = get_channel_layer()

async_to_sync(channel_layer.group_send)("trends", {
    "type": "trend_update",
    "message": {
        "type": "trend_update",
        "payload": { "entities": [...] }
    }
})
```

---

## CORS

Add `http://localhost:5173` to `CORS_ALLOWED_ORIGINS` in `settings.py`.

```python
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
]
```

---

## Environment Variables (Frontend)

Create `frontend/.env.local` to override defaults:

```env
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws/trends/
```
