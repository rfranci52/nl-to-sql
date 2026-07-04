"""HTTP layer for Tony's Pizza — wraps the engine so a browser can call it.

A thin FastAPI app. POST /api/ask with a plain-English question and get back the
interpretation, the SQL, and the rows (or a clarify question). It's the same
answer() proven in engine.py, now reachable over HTTP — which is what the React
UI, and later a Vercel serverless function, will call.

Run locally:  uv run uvicorn api:app --reload --port 8000
Docs:         http://localhost:8000/docs
"""

import traceback

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
import engine

db.seed()  # make sure pizza.db exists (idempotent — no-op if already seeded)

app = FastAPI(title=f"{db.SHOP_NAME} — plain-English -> SQL")

# Local dev: the Vite React app (5173) calls this API. Add the deployed origin
# here when you go live (or serve both from one origin and drop CORS entirely).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Clickable starting points for the UI — solves "what do I type?" for a visitor.
EXAMPLES = [
    "Who are our top 5 customers by how much they've spent?",
    "Which pizza sells the most?",
    "How many orders came from Brooklyn last month?",
    "What's the average order total?",
]


class AskRequest(BaseModel):
    question: str


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "shop": db.SHOP_NAME}


@app.get("/api/examples")
def examples() -> dict:
    return {"shop": db.SHOP_NAME, "examples": EXAMPLES}


@app.post("/api/ask")
def ask(req: AskRequest) -> dict:
    """Plain-English question -> {interpretation, sql, columns, rows} or a clarify."""
    question = req.question.strip()
    if not question:
        return {"ok": False, "clarify": "Ask a question about the pizza shop."}
    try:
        return engine.answer(question)
    except Exception:
        # Log the real error to the server console; keep the visitor's reply clean.
        traceback.print_exc()
        return {"ok": False,
                "clarify": "Something went wrong answering that — please try again."}
