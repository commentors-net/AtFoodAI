# ATFOOD

ATFOOD is a flavor-first AI kitchen assistant.

## What this repo contains
- Vanilla JS frontend AI trigger
- FastAPI backend using OpenAI Responses API
- Action-based prompt routing

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

export OPENAI_API_KEY=YOUR_KEY
export DATABASE_URI=YOUR_DATABASE_URI
uvicorn backend.app:app --reload --port 8000
```

## Frontend
Add `data-atfood-action` attributes to buttons/links and include `marked.min.js`, `atfood-config.js`, and `atfood-ai.js`.
Render output into `#atfood-ai-slot` (default renderer), or mount your own renderer with `window.ATFOOD_AI.mount`.
Trigger programmatically with `window.ATFOOD_AI.sendAction(payload)`.
Set the backend URL and base path in `frontend/atfood-config.js`.
For production under `/atfoodai`, the config sets `window.ATFOOD_BASE_PATH = "/atfoodai"` and routes API calls to `/atfoodai/api/atfood`.
Markdown rendering uses `marked.parse()` from `frontend/marked.min.js`.
Provide the API token via `window.ATFOOD_API_TOKEN` or `data-atfood-token` on the clicked element (optional if token auth is disabled).
Provide the user id via `window.ATFOOD_USER` or `data-atfood-user` on the clicked element (optional; defaults to requester IP).

Example:
```html
<button
  data-atfood-action="world_picks"
  data-atfood-user="user-123"
>
  Get world picks
</button>
<div id="atfood-ai-slot"></div>
<script>
  window.ATFOOD_API_TOKEN = "your-token";
  window.ATFOOD_USER = "user-123";
</script>
<script src="./atfood-config.js"></script>
<script src="./marked.min.js"></script>
<script src="./atfood-ai.js"></script>
```

## Backend
POST `/api/atfood` with JSON:
```json
{
  "action": "world_picks",
  "user_text": "optional text input",
  "recipe_id": "optional recipe id",
  "critic_topic": "optional critic topic",
  "session_id": "optional session id",
  "prefs": { "optional": "structured prefs" }
}
```

Optional headers:
```
X-ATFOOD-TOKEN: <your token>
X-ATFOOD-USER: <user id>
```

Returns:
```json
{
  "text": "...",
  "prompt_tokens": 123,
  "response_tokens": 456,
  "total_cost": 0.0177
}
```
