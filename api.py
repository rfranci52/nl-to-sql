"""HTTP layer for Tony's Pizza: wraps the engine + order logic for the browser.

Endpoints (all under /api):
  GET  /health    : liveness
  GET  /examples  : clickable starter questions
  GET  /menu      : the menu + current stock, for the ordering dropdowns
  POST /ask       : plain-English question -> interpretation + SQL + rows (read-only)
  POST /order     : place an order (write path)
  POST /restock   : refill inventory to baseline (the nightly cron target)

Reads go through the read-only pizza_ro role, writes through pizza_rw.
Run locally:  uv run uvicorn api:app --reload --port 8000
"""

import os
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import engine
import orders

app = FastAPI(title=f"{engine.SHOP_NAME}: plain-English -> SQL")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

EXAMPLES = [
    "Which category brings in the most revenue?",
    "Which item sells the most?",
    "Who are our top 5 customers by spend?",
    "How many drinks do we have in stock?",
]

CRON_SECRET = os.environ.get("CRON_SECRET", "")  # protects /restock in production


class AskRequest(BaseModel):
    question: str


class OrderLine(BaseModel):
    menu_item_id: int
    quantity: int


class OrderRequest(BaseModel):
    items: list[OrderLine]
    name: str | None = None


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "shop": engine.SHOP_NAME}


@app.get("/api/examples")
def examples() -> dict:
    return {"shop": engine.SHOP_NAME, "examples": EXAMPLES}


@app.get("/api/menu")
def menu() -> dict:
    return {"menu": orders.get_menu()}


@app.post("/api/ask")
def ask(req: AskRequest) -> dict:
    """Plain-English question -> {interpretation, sql, columns, rows} or a clarify."""
    question = req.question.strip()
    if not question:
        return {"ok": False, "clarify": "Ask a question about the pizza shop."}
    try:
        return engine.answer(question)
    except Exception:
        traceback.print_exc()
        return {"ok": False,
                "clarify": "Something went wrong answering that, please try again."}


@app.post("/api/order")
def order(req: OrderRequest) -> dict:
    """Place an order: validate, decrement stock, record it. Returns the receipt."""
    try:
        items = [{"menu_item_id": li.menu_item_id, "quantity": li.quantity} for li in req.items]
        return orders.place_order(items, req.name)
    except Exception:
        traceback.print_exc()
        return {"ok": False, "error": "Couldn't place the order, please try again."}


@app.post("/api/restock")
def restock(request: Request):
    """Refill inventory to baseline. Guarded by CRON_SECRET when one is set."""
    if CRON_SECRET and request.headers.get("x-cron-secret") != CRON_SECRET:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    return {"ok": True, "restocked": orders.restock()}
