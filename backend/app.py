from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, Deque, Dict, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, Field
import pymysql

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPEN_MODEL = os.getenv("OPEN_MODEL")
ATFOOD_MODEL = os.getenv("ATFOOD_MODEL")
ATFOOD_API_TOKEN = os.getenv("ATFOOD_API_TOKEN")
ATFOOD_CORS_ORIGINS = os.getenv("ATFOOD_CORS_ORIGINS", "")
DATABASE_URI = os.getenv("DATABASE_URI")
OPEN_INPUT_PRICE_PER_1K = Decimal(os.getenv("OPEN_INPUT_PRICE_PER_1K", "0"))
OPEN_OUTPUT_PRICE_PER_1K = Decimal(os.getenv("OPEN_OUTPUT_PRICE_PER_1K", "0"))

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is required")
if not OPEN_MODEL and not ATFOOD_MODEL:
    raise RuntimeError("OPEN_MODEL or ATFOOD_MODEL is required")
if not DATABASE_URI:
    raise RuntimeError("DATABASE_URI is required")

CLIENT = OpenAI(api_key=OPENAI_API_KEY)
MODEL = ATFOOD_MODEL or OPEN_MODEL

BASE_INSTRUCTIONS = """You are ATFOOD: a chef's curiosity + a critic's honesty.

Voice: punchy, friendly, flavor-first. No diet lecture. No boring.

Default output: concise sections, practical steps, real timing, and the "why" behind technique.

Global rules:

- Flavor first; nutrition included quietly (optional, never preachy).
- If user gives constraints (allergies, sodium, sugar, macros, IBS, etc.), adapt without losing the "soul" of the dish.
- Always offer smart swaps + a "don't ruin it" warning when a swap changes technique.
- Prefer actionable structure:
  1) The move (what makes it taste great)
  2) Ingredients (with swaps)
  3) Steps (timed)
  4) Variations (diet/health constraints)
  5) Shopping notes (optional)
- Ask at most ONE clarifying question if needed. If not needed, proceed.

Safety:

- For medical conditions: provide general guidance and suggest professional advice for medical decisions.

Routing:

You will receive an ACTION label and optional CONTEXT. Follow the action style:

ACTION=open_ai_kitchen

- Welcome + ask for: dish, servings, time, equipment, constraints, "non-negotiables".
- Offer 3 quick suggestions the user can pick (e.g., "Low-sodium without bland", "High-protein version", "Gluten-free with crunch").

ACTION=world_picks

- Give a short "compass for flavor": 6-10 picks (cuisine, ingredient, technique).
- Include "order/skip" style critic notes when relevant.

ACTION=food_era

- Create a 2-week theme plan: 1 theme, 3 sauces, 2 techniques, 6 dishes to practice.
- Add a "flavor kit shelf" list of staples.

ACTION=adjust_recipe

- Adapt the named recipe. Output:
  - Baseline essence (what must stay)
  - Modified recipe (steps + timing)
  - Swap table (ingredient -> swap -> flavor impact)
  - Optional: nutrition knobs (sodium/sugar/macros) explained simply.

ACTION=critic_notes

- Write like a friend with taste: quick, punchy callouts.
- Provide: "Order this / skip that / why it's worth it / how to spot the good version".
"""

RECIPE_CONTEXT = {
    "chili_crisp_noodles": {
        "title": "15-minute chili crisp noodles",
        "blurb": "Heat, crunch, and a sauce that clings. Easy to adapt: vegan, low-sodium, or extra protein.",
    },
    "charred_lemon_chicken": {
        "title": "Charred lemon chicken + herbs",
        "blurb": "Weeknight-perfect, restaurant aroma. Works with thighs, breasts, tofu, or cauliflower.",
    },
    "silky_tomato_soup": {
        "title": "Silky tomato soup (no sadness)",
        "blurb": "Roast the tomatoes. Finish with a bright acid. The difference is night and day.",
    },
}

ACTION_PROMPTS = {
    "open_ai_kitchen": lambda req: (
        "ACTION=open_ai_kitchen\n"
        "Goal: Onboard user into AI Kitchen and collect dish + constraints.\n"
        f"User text: {req.user_text or ''}\n"
    ),
    "world_picks": lambda req: (
        "ACTION=world_picks\n"
        "Goal: Give 'world picks' + a flavor compass. Ask what they want to explore.\n"
        f"User text: {req.user_text or ''}\n"
    ),
    "food_era": lambda req: (
        "ACTION=food_era\n"
        "Goal: Build a two-week 'food era' plan with sauces/techniques/dishes.\n"
        f"User text: {req.user_text or ''}\n"
    ),
    "adjust_recipe": lambda req: (
        "ACTION=adjust_recipe\n"
        "Goal: Adapt this recipe without losing soul.\n"
        f"Recipe: {RECIPE_CONTEXT.get(req.recipe_id or '', {}).get('title', req.recipe_id)}\n"
        f"Recipe blurb: {RECIPE_CONTEXT.get(req.recipe_id or '', {}).get('blurb', '')}\n"
        f"User constraints/request: {req.user_text or ''}\n"
        "If user gave no constraints, propose 3 good adaptation directions.\n"
    ),
    "critic_notes": lambda req: (
        "ACTION=critic_notes\n"
        "Goal: Punchy critic note expansion.\n"
        f"Topic: {req.critic_topic or ''}\n"
        f"User text: {req.user_text or ''}\n"
    ),
}

RATE_LIMIT_REQUESTS = int(os.getenv("ATFOOD_RATE_LIMIT_REQUESTS", "30"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("ATFOOD_RATE_LIMIT_WINDOW_SECONDS", "60"))

_rate_buckets: Dict[str, Deque[float]] = defaultdict(deque)

@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_conversation_table()
    _ensure_conversation_columns()
    yield


app = FastAPI(lifespan=lifespan)

cors_origins = [origin.strip() for origin in ATFOOD_CORS_ORIGINS.split(",") if origin.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-ATFOOD-TOKEN", "X-ATFOOD-USER"],
    )


class AtfoodRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=64)
    user_text: Optional[str] = None
    recipe_id: Optional[str] = None
    critic_topic: Optional[str] = None
    session_id: Optional[str] = None
    prefs: Optional[Dict[str, Any]] = None


class AtfoodResponse(BaseModel):
    text: str
    prompt_tokens: int
    response_tokens: int
    total_cost: Decimal


def _parse_database_uri(uri: str) -> dict:
    parsed = urlparse(uri)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise RuntimeError("DATABASE_URI must be a MySQL connection string")
    if not parsed.hostname or not parsed.username or not parsed.path:
        raise RuntimeError("DATABASE_URI is missing required fields")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": parsed.username,
        "password": parsed.password or "",
        "database": parsed.path.lstrip("/"),
    }


def _get_db_connection():
    cfg = _parse_database_uri(DATABASE_URI)
    return pymysql.connect(
        host=cfg["host"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        port=cfg["port"],
        charset="utf8mb4",
        autocommit=True,
    )


def _ensure_conversation_table():
    with _get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS atfood_conversations (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    user_id VARCHAR(128) NOT NULL,
                    action VARCHAR(64) NOT NULL,
                    prompt TEXT NOT NULL,
                    response_text TEXT NOT NULL,
                    prompt_tokens INT DEFAULT 0,
                    response_tokens INT DEFAULT 0,
                    total_cost DECIMAL(12,6) DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_user_created (user_id, created_at)
                )
                """
            )


def _ensure_conversation_columns():
    required_columns = {
        "prompt_tokens": "INT DEFAULT 0",
        "response_tokens": "INT DEFAULT 0",
        "total_cost": "DECIMAL(12,6) DEFAULT 0",
    }
    with _get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'atfood_conversations'
                """,
                (conn.db.decode() if isinstance(conn.db, bytes) else conn.db,),
            )
            existing = {row[0] for row in cursor.fetchall()}
            for column, definition in required_columns.items():
                if column in existing:
                    continue
                cursor.execute(
                    f"ALTER TABLE atfood_conversations ADD COLUMN {column} {definition}"
                )


def _store_conversation(
    user_id: str,
    action: str,
    prompt: str,
    response_text: str,
    prompt_tokens: int,
    response_tokens: int,
    total_cost: Decimal,
) -> None:
    with _get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO atfood_conversations (
                    user_id,
                    action,
                    prompt,
                    response_text,
                    prompt_tokens,
                    response_tokens,
                    total_cost
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    action,
                    prompt,
                    response_text,
                    prompt_tokens,
                    response_tokens,
                    str(total_cost),
                ),
            )


def enforce_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    window = _rate_buckets[client_ip]
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    window.append(now)


def extract_output_text(response) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text
    output = getattr(response, "output", None) or []
    parts = []
    for item in output:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "output_text":
                parts.append(getattr(content, "text", ""))
    return "".join(parts)


@app.post("/api/atfood", response_model=AtfoodResponse)
def atfood_endpoint(
    payload: AtfoodRequest,
    request: Request,
    x_atfood_token: Optional[str] = Header(None, alias="X-ATFOOD-TOKEN"),
    x_atfood_user: Optional[str] = Header(None, alias="X-ATFOOD-USER"),
) -> AtfoodResponse:
    if ATFOOD_API_TOKEN and x_atfood_token:
        if x_atfood_token != ATFOOD_API_TOKEN:
            raise HTTPException(status_code=401, detail="Invalid token")

    client_ip = request.client.host if request.client else "unknown"
    enforce_rate_limit(client_ip)

    prompt_builder = ACTION_PROMPTS.get(payload.action)
    if not prompt_builder:
        raise HTTPException(status_code=400, detail="Unknown action")

    prompt = prompt_builder(payload)
    if payload.prefs:
        prompt = f"{prompt}Prefs: {payload.prefs}\n"
    if payload.session_id:
        prompt = f"{prompt}Session: {payload.session_id}\n"

    response = CLIENT.responses.create(
        model=MODEL,
        instructions=BASE_INSTRUCTIONS,
        input=prompt,
    )
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "input_tokens", None)
    if prompt_tokens is None:
        prompt_tokens = getattr(usage, "prompt_tokens", 0)
    response_tokens = getattr(usage, "output_tokens", None)
    if response_tokens is None:
        response_tokens = getattr(usage, "completion_tokens", 0)
    prompt_tokens = int(prompt_tokens or 0)
    response_tokens = int(response_tokens or 0)
    total_cost = (
        (Decimal(prompt_tokens) * OPEN_INPUT_PRICE_PER_1K)
        + (Decimal(response_tokens) * OPEN_OUTPUT_PRICE_PER_1K)
    ) / Decimal("1000")
    text = extract_output_text(response).strip()
    if not text:
        raise HTTPException(status_code=502, detail="Empty response from model")
    user_id = (x_atfood_user or "").strip() or client_ip or "demo-user"
    try:
        _store_conversation(
            user_id,
            payload.action,
            prompt,
            text,
            prompt_tokens,
            response_tokens,
            total_cost,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to save conversation")
    return AtfoodResponse(
        text=text,
        prompt_tokens=prompt_tokens,
        response_tokens=response_tokens,
        total_cost=total_cost,
    )
