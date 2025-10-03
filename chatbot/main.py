import os
import re
import hmac
import logging
from typing import Optional, List
from datetime import datetime
from itertools import islice

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from openai import OpenAI, APIConnectionError, RateLimitError, APIStatusError
from dotenv import load_dotenv

# ---------------- Env & Logging ----------------
load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tomatoai")

# Environment (tolerant so import never crashes)
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT  = float(os.getenv("OPENAI_TIMEOUT", "10"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "300"))

SHARED_SECRET   = os.getenv("SHARED_SECRET", "dev-secret")

MONGO_URI       = os.getenv("MONGO_URI")
DB_NAME         = os.getenv("DB_NAME", "food-delivery")

USE_MEMORY      = os.getenv("USE_MEMORY", "0") == "1"  # default off on Vercel
FORCE_LLM       = os.getenv("FORCE_LLM", "0") == "1"   # optional: let LLM rewrite deterministic drafts

# OpenAI client (safe init; do NOT crash if missing/misconfigured)
client = None
if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY, timeout=OPENAI_TIMEOUT)
    except Exception as e:
        log.exception("OpenAI client init failed: %s", e)
        client = None
else:
    log.error("OPENAI_API_KEY not set; LLM replies will be disabled")

# Mongo (safe init)
mongo = None
db = None
try:
    if MONGO_URI:
        mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        db = mongo[DB_NAME]
except Exception:
    mongo = None
    db = None

# Optional memory
memory = None
if USE_MEMORY:
    try:
        from mem0 import Memory
        memory = Memory.from_config({
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "host": os.getenv("QDRANT_HOST", "localhost"),
                    "port": int(os.getenv("QDRANT_PORT", "6333")),
                }
            }
        })
    except Exception as e:
        log.warning("Mem0 disabled: %s", e)
        memory = None

# ---------------- Settings ----------------
MAX_MSG_LEN       = int(os.getenv("MAX_MSG_LEN", "2000"))
MAX_POPULAR       = int(os.getenv("MAX_POPULAR", "5"))
MAX_RECENT        = int(os.getenv("MAX_RECENT", "5"))
POPULARITY_START  = 50

# ---------------- Stripe error knowledge ----------------
def _n(s: str) -> str:
    return (s or "").strip().lower().replace("-", "_").replace(" ", "_")

STRIPE_ERRORS = {
    "types": {
        "card_error": "A problem with the card (number, CVC, expiry, or bank declined the charge). Show a friendly message and ask for another payment method or to contact the bank.",
        "invalid_request_error": "Your request to Stripe is malformed or missing params. Double-check IDs, amounts, currency, and required fields.",
        "api_error": "Stripe had an internal error. Safe to retry after a brief delay.",
        "rate_limit_error": "Too many requests too quickly. Back off and retry with exponential delay.",
        "authentication_error": "Invalid or missing API key / account isn’t authorized. Verify the key (test vs live) and the account permissions.",
        "permission_error": "The API key doesn’t have permission for that resource. Check Connect scopes / restricted keys.",
        "idempotency_error": "Reused idempotency key with different params. Generate a fresh key for a new request.",
        "api_connection_error": "Network problem reaching Stripe. Retry with backoff and verify TLS/network.",
        "oauth_error": "OAuth / Connect authorization issue. Reconnect the account and verify the client settings.",
    },
    "codes": {
        "resource_missing": "The object ID you referenced doesn’t exist or isn’t accessible. Verify the ID and environment (test vs live).",
        "parameter_missing": "A required parameter is missing. Add the missing field and retry.",
        "parameter_invalid_integer": "Invalid numeric field. Provide a valid integer for amount/quantity.",
        "amount_too_small": "Amount is below Stripe’s minimum for that currency. Increase the amount.",
        "amount_too_large": "Amount exceeds allowed maximum. Lower the amount.",
        "currency_mismatch": "You’re mixing currencies for related objects. Keep currencies consistent.",
        "country_unsupported": "The country isn’t supported for this method. Use a supported country or method.",
        "testmode_charges_only": "Using a test key in live mode (or vice versa). Switch to the correct key.",
        "charge_already_captured": "The charge is already captured. Don’t capture again.",
        "charge_already_refunded": "The charge is already refunded. Avoid duplicate refunds.",
        "payment_intent_unexpected_state": "PaymentIntent is in a state that doesn’t allow the action. Fetch its latest status and follow the lifecycle.",
        "setup_intent_unexpected_state": "SetupIntent can’t perform the action in its current status. Refresh and follow the required step.",
        "authentication_required": "Strong customer authentication (3DS) is required. Present 3D Secure to complete the payment.",
        "invalid_number": "The card number is incorrect. Ask the customer to re-enter the card.",
        "invalid_expiry_month": "Invalid expiry month.",
        "invalid_expiry_year": "Invalid expiry year.",
        "invalid_cvc": "CVC format is invalid.",
        "incorrect_cvc": "CVC doesn’t match. Customer should re-enter CVC or use another card.",
        "expired_card": "The card is expired. Use a different card.",
        "card_declined": "The bank declined the charge. See decline_code for details or ask the customer to try another card.",
        "processing_error": "An error occurred while processing the card. It’s usually transient—retry once, then try another method.",
    },
    "decline_codes": {
        "insufficient_funds": "The card has insufficient funds. Ask the customer to use another card or contact their bank.",
        "lost_card": "The card was reported lost. The bank blocked it. Use a different card.",
        "stolen_card": "The card was reported stolen. The bank blocked it. Use a different card.",
        "do_not_honor": "The bank declined without a reason. Ask the customer to contact their bank or use another card.",
        "generic_decline": "A generic decline from the bank. Try again later or a different method.",
        "pickup_card": "The bank requests to retain the card (severe flag). Use another card.",
        "reenter_transaction": "Try entering the transaction again. If it repeats, use another card.",
        "try_again_later": "Temporary issue at the bank. Try again later or another method.",
        "fraudulent": "The bank suspects fraud. Use another card; advise the customer to contact their bank.",
        "authentication_required": "3DS authentication required. Present 3D Secure to complete the payment.",
        "incorrect_zip": "ZIP/postal code didn’t match. Ask the customer to confirm their billing ZIP.",
        "incorrect_address": "Address check failed. Confirm the billing address.",
        "incorrect_cvc": "CVC didn’t match. Re-enter CVC.",
        "processor_declined": "The processor declined the charge. Use another card or contact bank.",
    },
    "intents": {
        "requires_payment_method": "Payment failed or was canceled. Collect a new payment method and confirm again.",
        "requires_confirmation": "You created/updated the intent. Now call confirm to continue.",
        "requires_action": "Customer action (e.g., 3DS) required. Use next_action to complete authentication.",
        "processing": "Stripe is processing the payment. Poll or wait for webhook to finalize.",
        "succeeded": "Payment succeeded. You can fulfill the order.",
        "canceled": "The intent was canceled. Create a new intent to retry.",
    },
}

def is_stripe_query(text: str) -> bool:
    t = (text or "").lower()
    if "stripe" in t or "payment_intent" in t or "setup_intent" in t:
        return True
    if "decline_code" in t or "card_declined" in t:
        return True
    for kw in ("error:", "code:", "type:", "payment_method", "3ds", "3d secure", "authentication_required"):
        if kw in t:
            return True
    for key in list(STRIPE_ERRORS["types"].keys()) + list(STRIPE_ERRORS["codes"].keys()) + list(STRIPE_ERRORS["decline_codes"].keys()):
        if key.replace("_", " ") in t or key in t:
            return True
    return False

def explain_stripe_error(text: str) -> str:
    t = (text or "")
    low = t.lower()

    found_types = []
    found_codes = []
    found_declines = []
    found_statuses = []

    for k in STRIPE_ERRORS["types"]:
        if k in low:
            found_types.append(k)
    for k in STRIPE_ERRORS["codes"]:
        if k in low:
            found_codes.append(k)
    for k in STRIPE_ERRORS["decline_codes"]:
        if k in low:
            found_declines.append(k)
    for k in STRIPE_ERRORS["intents"]:
        if k in low:
            found_statuses.append(k)

    for m in re.finditer(r'(type|code|decline_code)\s*["\':]\s*["\']?([a-zA-Z0-9_\-]+)', t):
        field = _n(m.group(1))
        val = _n(m.group(2))
        if field == "type" and val in STRIPE_ERRORS["types"] and val not in found_types:
            found_types.append(val)
        elif field == "code" and val in STRIPE_ERRORS["codes"] and val not in found_codes:
            found_codes.append(val)
        elif field == "decline_code" and val in STRIPE_ERRORS["decline_codes"] and val not in found_declines:
            found_declines.append(val)

    lines: List[str] = []

    if ("card_declined" in found_codes or "card_error" in found_types) and found_declines:
        dc = found_declines[0]
        lines.append(f"Stripe says **card_declined / {dc}** — {STRIPE_ERRORS['decline_codes'][dc]}")
    if found_codes and not lines:
        c = found_codes[0]
        lines.append(f"Stripe **{c}** — {STRIPE_ERRORS['codes'][c]}")
    if found_types and not lines:
        ty = found_types[0]
        lines.append(f"Stripe **{ty}** — {STRIPE_ERRORS['types'][ty]}")
    if found_statuses:
        st = found_statuses[0]
        lines.append(f"Status **{st}** — {STRIPE_ERRORS['intents'][st]}")

    if not lines:
        lines = [
            "I can help with Stripe errors. If you paste the JSON (type / code / decline_code), I’ll decode it.",
            "Common cases:",
            "• **card_declined** → bank rejected charge (see decline_code like insufficient_funds, do_not_honor).",
            "• **authentication_required** → present 3DS (next_action).",
            "• **invalid_request_error** → missing/wrong params; check IDs, currency, amounts.",
            "• **rate_limit_error** → back off and retry.",
        ]

    lines.append("Next steps: confirm the exact error fields, retry only idempotently, or collect a new payment method / contact bank if it’s a decline.")
    reply = " ".join(lines)
    if len(reply) > 900:
        reply = reply[:900].rstrip() + "…"
    return reply

# ---------------- Seed Data (normalized) ----------------
STATIC_FOODS = [
    {"name": "Greek salad", "category": "salad", "price": 12},
    {"name": "Veg salad", "category": "salad", "price": 18},
    {"name": "Clover Salad", "category": "salad", "price": 16},
    {"name": "Chicken Salad", "category": "salad", "price": 24},
    {"name": "Lasagna Rolls", "category": "rolls", "price": 14},
    {"name": "Peri Peri Rolls", "category": "rolls", "price": 12},
    {"name": "Chicken Rolls", "category": "rolls", "price": 20},
    {"name": "Veg Rolls", "category": "rolls", "price": 15},
    {"name": "Ripple Ice Cream", "category": "desserts", "price": 14},
    {"name": "Fruit Ice Cream", "category": "desserts", "price": 22},
    {"name": "Jar Ice Cream", "category": "desserts", "price": 10},
    {"name": "Vanilla Ice Cream", "category": "desserts", "price": 12},
    {"name": "Chicken Sandwich", "category": "sandwich", "price": 12},
    {"name": "Vegan Sandwich", "category": "sandwich", "price": 18},
    {"name": "Grilled Sandwich", "category": "sandwich", "price": 16},
    {"name": "Bread Sandwich", "category": "sandwich", "price": 24},
    {"name": "Cup Cake", "category": "cake", "price": 14},
    {"name": "Vegan Cake", "category": "cake", "price": 12},
    {"name": "Butterscotch Cake", "category": "cake", "price": 20},
    {"name": "Sliced Cake", "category": "cake", "price": 15},
    {"name": "Garlic Mushroom", "category": "veg", "price": 14},
    {"name": "Fried Cauliflower", "category": "veg", "price": 22},
    {"name": "Mix Veg Pulao", "category": "veg", "price": 10},
    {"name": "Rice Zucchini", "category": "veg", "price": 12},
    {"name": "Cheese Pasta", "category": "pasta", "price": 12},
    {"name": "Tomato Pasta", "category": "pasta", "price": 18},
    {"name": "Creamy Pasta", "category": "pasta", "price": 16},
    {"name": "Chicken Pasta", "category": "pasta", "price": 24},
    {"name": "Butter Noodles", "category": "noodles", "price": 14},
    {"name": "Veg Noodles", "category": "noodles", "price": 12},
    {"name": "Somen Noodles", "category": "noodles", "price": 20},
    {"name": "Cooked Noodles", "category": "noodles", "price": 15},
]

def bootstrap_foods_if_empty():
    if db is None:
        return
    try:
        if db["foods"].estimated_document_count() >= 10:
            return
        for item in STATIC_FOODS:
            doc = {
                "name": item["name"].strip(),
                "category": item["category"].strip().lower(),
                "price": float(item["price"]),
                "orders": POPULARITY_START,
            }
            db["foods"].update_one({"name": doc["name"]}, {"$set": doc}, upsert=True)
        try:
            db["foods"].create_index([("category", 1)])
            db["foods"].create_index([("orders", -1)])
            db["foods"].create_index([("name", 1)], unique=True)
        except Exception:
            pass
        log.info("Bootstrapped foods (%d docs).", db["foods"].count_documents({}))
    except Exception:
        log.exception("bootstrap_foods_if_empty failed")

# ---------------- Utilities ----------------
def safe_eq(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())

def trim_text(s: str, n: int) -> str:
    s = (s or "").strip()
    return s[:n]

def take(iterable, n):
    return list(islice(iterable, n))

def _items_for(cat: str, limit: int = 20):
    if db is None:
        return []
    try:
        cur = db["foods"].find(
            {"category": {"$regex": f"^{cat}$", "$options": "i"}},
            {"name": 1, "price": 1, "category": 1}
        ).limit(limit)
        return [
            {"name": d.get("name"), "price": d.get("price"), "category": d.get("category")}
            for d in cur if d.get("name")
        ]
    except Exception:
        log.exception("_items_for(%s) failed", cat)
        return []

def _fmt_price(p):
    if p is None:
        return ""
    try:
        return f"${int(float(p))}" if float(p).is_integer() else f"${float(p)}"
    except Exception:
        return f"${p}"

def _fmt_items(items):
    return ", ".join(
        f"{it['name']} ({_fmt_price(it.get('price'))})" if it.get("price") is not None else f"{it['name']}"
        for it in items if it.get("name")
    )

def _names_for(cat: str, limit: int = 20) -> List[str]:
    items = _items_for(cat, limit)
    return [it["name"] for it in items]

def get_popular_items(limit=MAX_POPULAR) -> List[str]:
    if db is None:
        return []
    try:
        cur = db["foods"].find({}, {"name": 1}).sort("orders", -1).limit(limit)
        return [doc.get("name") for doc in cur if doc.get("name")]
    except PyMongoError:
        log.exception("get_popular_items failed")
        return []

def get_user_recent_orders(user_id: Optional[str], limit=MAX_RECENT) -> List[str]:
    if db is None or not user_id or user_id == "guest":
        return []
    try:
        cur = (
            db["orders"]
            .find({"user_id": user_id}, {"order_id": 1})
            .sort("order_date", -1)
            .limit(limit)
        )
        return [str(doc.get("order_id")) for doc in cur if doc.get("order_id") is not None]
    except PyMongoError:
        log.exception("get_user_recent_orders failed")
        return []

# Category helpers
def get_sandwich_names(limit=20): return _names_for("sandwich", limit)
def get_rolls_names(limit=20):    return _names_for("rolls", limit)
def get_salad_names(limit=20):    return _names_for("salad", limit)
def get_desserts_names(limit=20): return _names_for("desserts", limit)
def get_cake_names(limit=20):     return _names_for("cake", limit)
def get_pasta_names(limit=20):    return _names_for("pasta", limit)
def get_noodles_names(limit=20):  return _names_for("noodles", limit)
def get_veg_names(limit=20):      return _names_for("veg", limit)

SYNONYMS = {
    "sub": "sandwich", "subs": "sandwich", "hoagie": "sandwich",
    "wrap": "rolls", "wraps": "rolls",
    "desert": "desserts", "deserts": "desserts",
    "pure veg": "veg",
}
def category_from_query(text: str) -> Optional[str]:
    lower = text.lower()
    for raw in ("sandwich", "roll", "rolls", "salad", "dessert", "desserts", "cake", "pasta", "noodle", "noodles", "veg", "pure veg"):
        if raw in lower:
            if raw in ("roll",): return "rolls"
            if raw in ("dessert",): return "desserts"
            if raw in ("pure veg",): return "veg"
            return raw.rstrip("s")
    for alias, canon in SYNONYMS.items():
        if alias in lower:
            return canon
    return None

POPULAR_KEYWORDS = ("popular", "best", "bestseller", "most ordered", "most-ordered", "top", "famous", "hot")
def is_popularity_query(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in POPULAR_KEYWORDS)

def top_items_from_orders(limit: int = 3, category: Optional[str] = None) -> List[str]:
    if db is None:
        return []
    try:
        pipeline = [
            {"$unwind": "$items"},
            {"$addFields": {"qty": {"$ifNull": ["$items.qty", 1]}}},
        ]
        if category:
            cat_lower = category.lower()
            pipeline += [
                {
                    "$lookup": {
                        "from": "foods",
                        "let": {"itemName": "$items.name"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": [{"$toLower": "$name"}, {"$toLower": "$$itemName"}]},
                                            {"$eq": [{"$toLower": "$category"}, cat_lower]},
                                        ]
                                    }
                                }
                            },
                            {"$project": {"_id": 0, "name": 1}},
                        ],
                        "as": "food"
                    }
                },
                {"$match": {"food.0": {"$exists": True}}}
            ]
            group_key = "$food.name"
        else:
            group_key = {"$toLower": "$items.name"}

        pipeline += [
            {"$group": {"_id": group_key, "totalQty": {"$sum": "$qty"}}},
            {"$sort": {"totalQty": -1}},
            {"$limit": int(limit)},
        ]

        rows = list(db["orders"].aggregate(pipeline))
        names: List[str] = []
        for r in rows:
            name = r["_id"]
            if isinstance(name, list) and name:
                name = name[0]
            if isinstance(name, dict):
                name = name.get("name") or name.get("_id") or ""
            if isinstance(name, str) and name:
                names.append(name)
        return names
    except Exception:
        log.exception("top_items_from_orders failed (category=%s)", category)
        return []

def top_items_from_foods(limit: int = 3, category: Optional[str] = None) -> List[str]:
    if db is None:
        return []
    try:
        q = {}
        if category:
            q["category"] = {"$regex": f"^{category}$", "$options": "i"}
        cur = db["foods"].find(q, {"name": 1}).sort("orders", -1).limit(int(limit))
        return [d["name"] for d in cur if d.get("name")]
    except Exception:
        log.exception("top_items_from_foods failed (category=%s)", category)
        return []

def bump_food_orders(items: List[dict]):
    if db is None:
        return
    try:
        for it in items or []:
            name = (it.get("name") or "").strip()
            qty = int(it.get("qty") or 1)
            db["foods"].update_one({"name": name}, {"$inc": {"orders": qty}})
    except Exception:
        log.exception("bump_food_orders failed")

SYSTEM_PROMPT = (
    "You are TomatoAI, a concise, friendly customer support chatbot for a food delivery platform. "
    "You help with menus, delivery times, tracking orders, refunds, and reorders.\n\n"
    "Rules:\n"
    "1) Use ONLY details from the provided database context for specifics like dish names, order IDs, or status.\n"
    "2) If a specific detail is missing, ask ONE brief clarifying question or provide a safe generic step (e.g., how to check order status in-app).\n"
    "3) Never invent order numbers, times, or policies. No markdown tables; short paragraphs or bullets only.\n"
    "4) Keep answers under ~120 words unless the user explicitly asks for more.\n"
)

def llm_compose(system_prompt: str, content: str) -> str:
    if client is None:
        return content
    try:
        r = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            temperature=0.3,
            max_tokens=OPENAI_MAX_TOKENS,
        )
        out = (r.choices[0].message.content or "").strip()
        if not out:
            return content
        usage = getattr(r, "usage", None)
        if usage:
            log.info("openai tokens prompt=%s completion=%s total=%s",
                     getattr(usage, "prompt_tokens", None),
                     getattr(usage, "completion_tokens", None),
                     getattr(usage, "total_tokens", None))
        return out
    except (APIConnectionError, RateLimitError, APIStatusError) as e:
        log.error("llm_compose openai error type=%s msg=%s", type(e).__name__, str(e))
        return content
    except Exception as e:
        log.exception("llm_compose unexpected err=%s", e)
        return content

def build_context(user_msg: str, user_id: Optional[str]) -> str:
    mem_lines: List[str] = []
    if memory:
        try:
            results = memory.search(query=user_msg, user_id=user_id) or {}
            for m in take(results.get("results", []), 5):
                val = m.get("memory")
                if isinstance(val, str) and val.strip():
                    mem_lines.append(f"- {val.strip()[:160]}")
        except Exception:
            pass

    popular = take(get_popular_items(), MAX_POPULAR)
    recent = take(get_user_recent_orders(user_id), MAX_RECENT)

    sandwiches = _items_for("sandwich", MAX_POPULAR)
    rolls      = _items_for("rolls", MAX_POPULAR)
    veg        = _items_for("veg", MAX_POPULAR)
    desserts   = _items_for("desserts", MAX_POPULAR)
    salad      = _items_for("salad", MAX_POPULAR)
    cake       = _items_for("cake", MAX_POPULAR)
    pasta      = _items_for("pasta", MAX_POPULAR)
    noodles    = _items_for("noodles", MAX_POPULAR)

    ctx_parts: List[str] = []
    if mem_lines:  ctx_parts.append("Relevant past information:\n" + "\n".join(mem_lines))
    if popular:    ctx_parts.append("Popular dishes: " + ", ".join(popular))
    if recent:     ctx_parts.append("User recent orders: " + ", ".join(recent))
    if sandwiches: ctx_parts.append("Sandwich options: " + _fmt_items(sandwiches))
    if rolls:      ctx_parts.append("Rolls options: " + _fmt_items(rolls))
    if salad:      ctx_parts.append("Salad options: " + _fmt_items(salad))
    if desserts:   ctx_parts.append("Dessert options: " + _fmt_items(desserts))
    if cake:       ctx_parts.append("Cake options: " + _fmt_items(cake))
    if pasta:      ctx_parts.append("Pasta options: " + _fmt_items(pasta))
    if noodles:    ctx_parts.append("Noodles options: " + _fmt_items(noodles))
    if veg:        ctx_parts.append("Veg options: " + _fmt_items(veg))

    return ("Database context:\n" + "\n".join(ctx_parts) + "\n") if ctx_parts else ""

# -------- Previous Orders intent --------
_PREV_ORDERS_KEYWORDS = (
    "previous orders", "past orders", "order history", "my orders",
    "recent orders", "last order", "my last order", "history of orders",
    "show my orders", "show my previous orders", "show my recent orders"
)
def is_previous_orders_query(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in _PREV_ORDERS_KEYWORDS)

# ---------------- API Models ----------------
class ChatReq(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MSG_LEN)
    userId: Optional[str] = Field(default=None, max_length=120)

class ChatResp(BaseModel):
    reply: str

# ---------------- App ----------------
app = FastAPI(title="Tomato Chatbot API", version="1.6.1")

@app.on_event("startup")
def _seed_on_startup():
    bootstrap_foods_if_empty()

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    req_id = request.headers.get("x-request-id") or os.urandom(8).hex()
    request.state.req_id = req_id
    try:
        response = await call_next(request)
    except Exception as exc:
        log.exception("Unhandled crash req_id=%s path=%s", req_id, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"ok": False, "detail": "internal_error", "type": type(exc).__name__, "message": str(exc)},
            headers={"x-request-id": req_id},
        )
    response.headers["x-request-id"] = req_id
    return response

@app.get("/")
def root():
    return {"ok": True, "service": "Tomato Chatbot API", "routes": [r.path for r in app.routes]}

@app.get("/__routes")
def list_routes():
    return [r.path for r in app.routes]

@app.get("/debug")
def debug():
    try:
        import openai as _openai
        openai_ver = getattr(_openai, "__version__", "unknown")
    except Exception:
        openai_ver = "unknown"
    db_ok = False
    if mongo is not None:
        try:
            mongo.admin.command("ping")
            db_ok = True
        except Exception:
            db_ok = False
    return {
        "ok": True,
        "has_openai_key": bool(OPENAI_API_KEY),
        "client_is_none": client is None,
        "model": OPENAI_MODEL,
        "db": DB_NAME,
        "db_ok": db_ok,
        "force_llm": FORCE_LLM,
        "packages": {"openai": openai_ver},
    }

@app.get("/health")
def health():
    db_ok = False
    if mongo is not None:
        try:
            mongo.admin.command("ping")
            db_ok = True
        except Exception:
            db_ok = False
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "memory_enabled": bool(memory),
        "db": DB_NAME,
        "db_ok": db_ok,
        "model": OPENAI_MODEL,
        "version": "1.6.1",
        "force_llm": FORCE_LLM,
    }

# ---------------- Chat ----------------
@app.post("/chat", response_model=ChatResp)
async def chat(req: ChatReq, x_service_auth: str = Header(default=""), request: Request = None, response: Response = None):
    if not safe_eq(x_service_auth, SHARED_SECRET):
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_msg = trim_text(req.message, MAX_MSG_LEN)
    if not user_msg:
        raise HTTPException(status_code=400, detail="message is required")

    user_id = req.userId or None
    req_id = getattr(request.state, "req_id", "n/a")

    # Stripe branch
    if is_stripe_query(user_msg):
        reply = explain_stripe_error(user_msg)
        if response is not None:
            response.headers["X-Answer-Source"] = "rule:stripe"
        return ChatResp(reply=reply)

    # Previous orders branch (no frontend login UI; just instruct to log in on the website)
    if is_previous_orders_query(user_msg):
        if not user_id or user_id == "guest":
            text = "Please log in on the website to view your past orders. Once you’re signed in, ask “show my recent orders.”"
            if response is not None:
                response.headers["X-Answer-Source"] = "rule:orders_login_required"
            return ChatResp(reply=text)

        orders = get_user_recent_orders(user_id, limit=MAX_RECENT)
        if orders:
            text = "Your recent orders: " + ", ".join(orders) + ". Want details for one of them?"
            if response is not None:
                response.headers["X-Answer-Source"] = "rule:orders_list"
            return ChatResp(reply=text)
        else:
            text = "I couldn’t find past orders for your account yet. You can place an order and I’ll track it here."
            if response is not None:
                response.headers["X-Answer-Source"] = "rule:orders_none"
            return ChatResp(reply=text)

    # Popularity: DB → (optional) LLM compose
    if is_popularity_query(user_msg):
        cat = category_from_query(user_msg)
        names = top_items_from_orders(limit=3, category=cat) or top_items_from_foods(limit=3, category=cat)
        if names:
            draft = (
                f"Our most-ordered {cat.rstrip('s')} right now: " + ", ".join(names) + "."
                if cat else
                "Top items customers are ordering: " + ", ".join(names) + "."
            )
            if FORCE_LLM:
                db_context = build_context(user_msg, user_id)
                content = f"{db_context}\nCustomer: {user_msg}\nDraft: {draft}\nRewrite the draft for the customer, following the rules."
                draft = llm_compose(SYSTEM_PROMPT, content)
                if response is not None:
                    response.headers["X-Answer-Source"] = "rule+llm:popularity"
            else:
                if response is not None:
                    response.headers["X-Answer-Source"] = "rule:popularity"
            return ChatResp(reply=draft)

    # Category answers: DB → (optional) LLM compose
    cat = category_from_query(user_msg)
    if cat:
        fetch_map = {
            "sandwich": get_sandwich_names,
            "rolls":    get_rolls_names,
            "salad":    get_salad_names,
            "desserts": get_desserts_names,
            "cake":     get_cake_names,
            "pasta":    get_pasta_names,
            "noodles":  get_noodles_names,
            "veg":      get_veg_names,
        }
        fetcher = fetch_map.get(cat)
        if fetcher:
            names = fetcher(limit=20)
            if names:
                draft = f"Our {cat.rstrip('s')} options include: " + ", ".join(names[:10]) + "."
                if FORCE_LLM:
                    db_context = build_context(user_msg, user_id)
                    content = f"{db_context}\nCustomer: {user_msg}\nDraft: {draft}\nRewrite the draft for the customer, following the rules."
                    draft = llm_compose(SYSTEM_PROMPT, content)
                    if response is not None:
                        response.headers["X-Answer-Source"] = "rule+llm:category"
                else:
                    if response is not None:
                        response.headers["X-Answer-Source"] = "rule:category"
                return ChatResp(reply=draft)

    # Primary LLM path
    db_context = build_context(user_msg, user_id)
    full_prompt = f"{db_context}\nCustomer: {user_msg}\nSupport Agent:"

    if client is None:
        popular = top_items_from_orders(limit=10) or get_popular_items()
        if popular:
            if response is not None:
                response.headers["X-Answer-Source"] = "fallback:popular"
            return ChatResp(reply="I’m temporarily offline. Popular dishes: " + ", ".join(popular[:10]) + ".")
        raise HTTPException(status_code=503, detail="Assistant temporarily unavailable")

    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": full_prompt},
            ],
            temperature=0.3,
            max_tokens=OPENAI_MAX_TOKENS,
        )
        answer = (completion.choices[0].message.content or "").strip()
        if response is not None:
            response.headers["X-Answer-Source"] = "llm"
        usage = getattr(completion, "usage", None)
        if usage:
            log.info("openai tokens prompt=%s completion=%s total=%s",
                     getattr(usage, "prompt_tokens", None),
                     getattr(usage, "completion_tokens", None),
                     getattr(usage, "total_tokens", None))
    except (APIConnectionError, RateLimitError, APIStatusError) as e:
        log.error("openai error req_id=%s type=%s msg=%s", req_id, type(e).__name__, str(e))
        popular = get_popular_items()
        if popular:
            if response is not None:
                response.headers["X-Answer-Source"] = "fallback:popular_on_error"
            return ChatResp(reply="I’m having trouble reaching the assistant. Popular dishes: " + ", ".join(popular[:10]) + ".")
        raise HTTPException(status_code=502, detail="Chat service upstream error")
    except Exception as e:
        log.exception("openai unexpected req_id=%s err=%s", req_id, e)
        popular = get_popular_items()
        if popular:
            if response is not None:
                response.headers["X-Answer-Source"] = "fallback:popular_on_exception"
            return ChatResp(reply="I’m having trouble reaching the assistant. Popular dishes: " + ", ".join(popular[:10]) + ".")
        raise HTTPException(status_code=502, detail="Chat service upstream error")

    if memory and user_id:
        try:
            memory.add(user_msg, user_id=user_id, metadata={"app_id": "tomato", "role": "user"})
            memory.add(answer,   user_id=user_id, metadata={"app_id": "tomato", "role": "assistant"})
        except Exception:
            pass

    return ChatResp(reply=answer if isinstance(answer, str) else "")