# Scout API reference

Scout exposes a small REST API from `backend/search_api.py`. Run it locally with `npm run dev:api`; the default base URL is `http://localhost:8000`.

## Conventions

- Routes accept both trailing-slash and non-trailing-slash forms unless noted.
- JSON responses use UTF-8 and failures return `{ "error": "..." }` with a `400` or `404` status.
- The browser client uses the Vite proxy in development. Set `VITE_API_URL` when the API is hosted elsewhere.

## Read routes

| Route | Parameters | Returns |
| --- | --- | --- |
| `GET /api/health` | None | Service health and basic runtime status. |
| `GET /api/trends` | None | Ranked startup entities for the dashboard map. |
| `GET /api/sources` | None | Per-source pipeline metadata. |
| `GET /api/pipeline/status` | None | Scheduler and latest manual-run status. |
| `GET /api/debug/index-signature` | None | Search-index signature and metadata. |
| `GET /api/entity/{entity_key}/nodes` | `include_enriched`, `limit` | Source/evidence nodes for an entity. |
| `GET /api/entity/{entity_key}/history` | `window_days` | Historical data for an entity. |
| `GET /api/niche-search` | `query` or `q`, `limit`, `min_score`, `use_nemotron`, `enrich_on_demand`, `enrich_limit` | Ranked matches for an investor thesis. |
| `GET /api/user/profile` | `user_id` | Stored profile fields. |
| `GET /api/user/bookmarks` | `user_id` | Bookmarks for a user. |

## Write routes

### Run the pipeline

`POST /api/pipeline/run`

```json
{
  "mode": "manual",
  "do_backfill": true,
  "do_enrichment": true,
  "async": true
}
```

The API returns `409` if a manual run is already in progress. This route currently dispatches a prototype scheduler shim for dashboard compatibility; use `npm run pipeline:run` to execute the actual scraper pipeline.

### Search a niche

`POST /api/niche-search`

```json
{
  "query": "workflow automation for dental clinics",
  "limit": 12,
  "min_score": 10,
  "use_nemotron": true,
  "enrich_on_demand": false,
  "query_profile": {},
  "dimension_priority_rank": {}
}
```

`OPENROUTER_API_KEY` is required when Nemotron reranking is enabled.

### Profile and bookmarks

`POST /api/user/profile` and `POST /api/user/bookmarks` require an `X-User-ID` header. Profile requests accept `niche`, `bio`, `firm`, `location`, and `avatar_url`; bookmark requests accept an `entity_key` and toggle that bookmark.

## Authentication

Browser authentication is handled by Supabase. The API stores the profile and bookmark records in Scout's SQLite database and expects the browser client to send the authenticated user ID in `X-User-ID` for write requests.

## Runtime notes

This API is the current implementation. It is not a Django Channels or WebSocket service; clients use REST polling when they need fresh dashboard data.
