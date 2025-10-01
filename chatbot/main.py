import os
import re
import hmac
import logging
from typing import Optional, List
from datetime import datetime
from itertools import islice

from fastapi import FastAPI, Header, HTTPException, Request
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

# Environment (lazy / tolerant so import never crashes)
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT  = float(os.getenv("OPENAI_TIMEOUT", "10"))
SHARED_SECRET   = os.getenv("SHARED_SECRET", "dev-secret")

MONGO_URI       = os.getenv("MONGO_URI")
DB_NAME         = os.getenv("DB_NAME", "food-delivery")

USE_MEMORY      = os.getenv("USE_MEMORY", "0") == "1"  # default off on Vercel

# OpenAI client (safe init; do NOT crash if missing/misconfigured)
client = None
if OPENAI_API_KEY:
    try:
        client = OpenAI(timeout=OPENAI_TIMEOUT)  # client-level timeout only
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
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "300"))
POPULARITY_START  = 50

# ---------------- Stripe error knowledge ----------------
def _n(s: str) -> str:
    """normalize key: lower + replace spaces/dashes with underscores"""
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
    # 'code' (or older 'error.code')
    "codes": {
        # Request/param issues
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
        "authentication_required": "Strong customer authentication (3DS) is required. Present 3DS / next_action to the customer.",
        # Card details
        "invalid_number": "The card number is incorrect. Ask the customer to re-enter the card.",
        "invalid_expiry_month": "Invalid expiry month.",
        "invalid_expiry_year": "Invalid expiry year.",
        "invalid_cvc": "CVC format is invalid.",
        "incorrect_cvc": "CVC doesn’t match. Customer should re-enter CVC or use another card.",
        "expired_card": "The card is expired. Use a different card.",
        # Generic decline code included here as code in older flows
        "card_declined": "The bank declined the charge. See decline_code for details or ask the customer to try another card.",
        "processing_error": "An error occurred while processing the card. It’s usually transient—retry once, then try another method.",
    },
    # 'decline_code' (only when code == card_declined)
    "decline_codes": {
        "insufficient_funds": "The card has insufficient funds. Ask the customer to use another card or contact their bank.",
        "lost_card": "The card was reported lost. The bank blocked it. Use a different card.",
        "stolen_card": "The card was reported stolen. The bank blocked it. Use a different card.",
        "do_not_honor": "The bank declined without a reason. Ask the customer to contact their bank or use another card.",
        "generic_decline": "A generic decline from the bank. Try again later or use a different payment method.",
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
    # PaymentIntent/SetupIntent helpful statuses (not errors but common confusion)
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
    # if user pasted an error blob with these fields/words
    for kw in ("error:", "code:", "type:", "payment_method", "3ds", "3d secure", "authentication_required"):
        if kw in t:
            return True
    # any known key present
    for key in list(STRIPE_ERRORS["types"].keys()) + list(STRIPE_ERRORS["codes"].keys()) + list(STRIPE_ERRORS["decline_codes"].keys()):
        if key.replace("_", " ") in t or key in t:
            return True
    return False

def explain_stripe_error(text: str) -> str:
    """
    Parse the user text and produce a concise, actionable explanation.
    """
    t = (text or "")
    low = t.lower()

    found_types = []
    found_codes = []
    found_declines = []
    found_statuses = []

    # brute-force match known keys
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

    # also parse JSON-ish blobs like "decline_code": "insufficient_funds"
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

    # Priority: card_declined + decline_code combo
    if ("card_declined" in found_codes or "card_error" in found_types) and found_declines:
        dc = found_declines[0]
        lines.append(f"Stripe says **card_declined / {dc}** — {STRIPE_ERRORS['decline_codes'][dc]}")
    # Specific code
    if found_codes and not lines:
        c = found_codes[0]
        lines.append(f"Stripe **{c}** — {STRIPE_ERRORS['codes'][c]}")
    # Type fallback
    if found_types and not lines:
        ty = found_types[0]
        lines.append(f"Stripe **{ty}** — {STRIPE_ERRORS['types'][ty]}")
    # Intent statuses (helpful hint if present)
    if found_statuses:
        st = found_statuses[0]
        lines.append(f"Status **{st}** — {STRIPE_ERRORS['intents'][st]}")

    # Nothing matched → give a compact playbook
    if not lines:
        lines = [
            "I can help with Stripe errors. If you paste the JSON (type / code / decline_code), I’ll decode it.",
            "Common cases:",
            "• **card_declined** → bank rejected charge (see decline_code like insufficient_funds, do_not_honor).",
            "• **authentication_required** → present 3DS (next_action).",
            "• **invalid_request_error** → missing/wrong params; check IDs, currency, amounts.",
            "• **rate_limit_error** → back off and retry.",
        ]

    # Next steps (generic + safe)
    lines.append("Next steps: confirm the exact error fields, retry only idempotently, or collect a new payment method / contact bank if it’s a decline.")
    # Keep response compact
    reply = " ".join(lines)
    # Trim to ~120-160 words to stay concise
    if len(reply) > 900:
        reply = reply[:900].rstrip() + "…"
    return reply

# ---------------- Seed Data (normalized) ----------------
STATIC_FOODS = [
    # Salad
    {"name": "Greek salad", "category": "salad", "price": 12},
    {"name": "Veg salad", "category": "salad", "price": 18},
    {"name": "Clover Salad", "category": "salad", "price": 16},
    {"name": "Chicken Salad", "category": "salad", "price": 24},
    # Rolls
    {"name": "Lasagna Rolls", "category": "rolls", "price": 14},
    {"name": "Peri Peri Rolls", "category": "rolls", "price": 12},
    {"name": "Chicken Rolls", "category": "rolls", "price": 20},
    {"name": "Veg Rolls", "category": "rolls", "price": 15},
    # Desserts
    {"name": "Ripple Ice Cream", "category": "desserts", "price": 14},
    {"name": "Fruit Ice Cream", "category": "desserts", "price": 22},
    {"name": "Jar Ice Cream", "category": "desserts", "price": 10},
    {"name": "Vanilla Ice Cream", "category": "desserts", "price": 12},
    # Sandwich
    {"name": "Chicken Sandwich", "category": "sandwich", "price": 12},
    {"name": "Vegan Sandwich", "category": "sandwich", "price": 18},
    {"name": "Grilled Sandwich", "category": "sandwich", "price": 16},
    {"name": "Bread Sandwich", "category": "sandwich", "price": 24},
    # Cake
    {"name": "Cup Cake", "category": "cake", "price": 14},
    {"name": "Vegan Cake", "category": "cake", "price": 12},
    {"name": "Butterscotch Cake", "category": "cake", "price": 20},
    {"name": "Sliced Cake", "category": "cake", "price": 15},
    # Veg mains
    {"name": "Garlic Mushroom", "category": "veg", "price": 14},
    {"name": "Fried Cauliflower", "category": "veg", "price": 22},
    {"name": "Mix Veg Pulao", "category": "veg", "price": 10},
    {"name": "Rice Zucchini", "category": "veg", "price": 12},
    # Pasta
    {"name": "Cheese Pasta", "category": "pasta", "price": 12},
    {"name": "Tomato Pasta", "category": "pasta", "price": 18},
    {"name": "Creamy Pasta", "category": "pasta", "price": 16},
    {"name": "Chicken Pasta", "category": "pasta", "price": 24},
    # Noodles
    {"name": "Butter Noodles", "category": "noodles", "price": 14},
    {"name": "Veg Noodles", "category": "noodles", "price": 12},
    {"name": "Somen Noodles", "category": "noodles", "price": 20},
    {"name": "Cooked Noodles", "category": "noodles", "price": 15},
]

def bootstrap_foods_if_empty():
    """Upsert a small menu so DB answers work immediately."""
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

# Case-insensitive exact category match
def _names_for(cat: str, limit: int = 20) -> List[str]:
    if db is None:
        return []
    try:
        cur = db["foods"].find(
            {"category": {"$regex": f"^{cat}$", "$options": "i"}},
            {"name": 1}
        ).limit(limit)
        return [d["name"] for d in cur if d.get("name")]
    except Exception:
        log.exception("names_for(%s) failed", cat)
        return []

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
    if db is None or not user_id:
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

# Map query text to a canonical category
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

# -------- Popularity helpers --------
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
                {"$match": {"food.0": {"$exists": True}}},
                {"$addFields": {"canonName": {"$arrayElemAt": ["$food.name", 0]}}},
            ]
            group_key = "$canonName"
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
            if not name:
                continue
            db["foods"].update_one({"name": name}, {"$inc": {"orders": qty}})
    except Exception:
        log.exception("bump_food_orders failed")

# ---------------- API Models ----------------
class ChatReq(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MSG_LEN)
    userId: Optional[str] = Field(default=None, max_length=120)

class ChatResp(BaseModel):
    reply: str

# ---------------- App ----------------
app = FastAPI(title="Tomato Chatbot API", version="1.4.0")

@app.on_event("startup")
def _seed_on_startup():
    bootstrap_foods_if_empty()

# Middleware adds x-request-id and prevents raw 500s from leaking
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

# Friendly root + route lister
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
        "version": "1.4.0",
    }

SYSTEM_PROMPT = (
    "You are TomatoAI, a concise, friendly customer support chatbot for a food delivery platform. "
    "You help with menus, delivery times, tracking orders, refunds, and reorders.\n\n"
    "Rules:\n"
    "1) Use ONLY details from the provided database context for specifics like dish names, order IDs, or status.\n"
    "2) If a specific detail is missing, ask ONE brief clarifying question or provide a safe generic step (e.g., how to check order status in-app).\n"
    "3) Never invent order numbers, times, or policies. No markdown tables; short paragraphs or bullets only.\n"
    "4) Keep answers under ~120 words unless the user explicitly asks for more.\n"
)

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

    sandwiches = take(get_sandwich_names(), MAX_POPULAR)
    rolls      = take(get_rolls_names(), MAX_POPULAR)
    veg        = take(get_veg_names(), MAX_POPULAR)
    desserts   = take(get_desserts_names(), MAX_POPULAR)
    salad      = take(get_salad_names(), MAX_POPULAR)
    cake       = take(get_cake_names(), MAX_POPULAR)
    pasta      = take(get_pasta_names(), MAX_POPULAR)
    noodles    = take(get_noodles_names(), MAX_POPULAR)

    ctx_parts: List[str] = []
    if mem_lines:  ctx_parts.append("Relevant past information:\n" + "\n".join(mem_lines))
    if popular:    ctx_parts.append("Popular dishes: " + ", ".join(popular))
    if recent:     ctx_parts.append("User recent orders: " + ", ".join(recent))
    if sandwiches: ctx_parts.append("Sandwich options: " + ", ".join(sandwiches))
    if rolls:      ctx_parts.append("Rolls options: " + ", ".join(rolls))
    if salad:      ctx_parts.append("Salad options: " + ", ".join(salad))
    if desserts:   ctx_parts.append("Dessert options: " + ", ".join(desserts))
    if cake:       ctx_parts.append("Cake options: " + ", ".join(cake))
    if pasta:      ctx_parts.append("Pasta options: " + ", ".join(pasta))
    if noodles:    ctx_parts.append("Noodles options: " + ", ".join(noodles))
    if veg:        ctx_parts.append("Veg options: " + ", ".join(veg))

    return ("Database context:\n" + "\n".join(ctx_parts) + "\n") if ctx_parts else ""

# ---------------- Chat ----------------
@app.post("/chat", response_model=ChatResp)
async def chat(req: ChatReq, x_service_auth: str = Header(default=""), request: Request = None):
    # Auth
    if not safe_eq(x_service_auth, SHARED_SECRET):
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_msg = trim_text(req.message, MAX_MSG_LEN)
    if not user_msg:
        raise HTTPException(status_code=400, detail="message is required")

    user_id = req.userId or None
    req_id = getattr(request.state, "req_id", "n/a")

    # ---- Stripe errors branch (DB/LLM not needed) ----
    if is_stripe_query(user_msg):
        reply = explain_stripe_error(user_msg)
        return ChatResp(reply=reply)

    # ---- Popularity questions: answer from DB, no LLM needed ----
    if is_popularity_query(user_msg):
        cat = category_from_query(user_msg)
        names = top_items_from_orders(limit=3, category=cat) or top_items_from_foods(limit=3, category=cat)
        if names:
            if cat:
                return ChatResp(reply=f"Our most-ordered {cat.rstrip('s')} right now: " + ", ".join(names) + ".")
            else:
                return ChatResp(reply="Top items customers are ordering: " + ", ".join(names) + ".")

    # ---- Category-first answers (no LLM needed) ----
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
                return ChatResp(reply=f"Our {cat.rstrip('s')} options include: " + ", ".join(names[:10]) + ".")

    # ---- Build LLM context ----
    db_context = build_context(user_msg, user_id)
    full_prompt = f"{db_context}\nCustomer: {user_msg}\nSupport Agent:"

    # ---- Graceful fallback if LLM unavailable ----
    if client is None:
        popular = top_items_from_orders(limit=10) or get_popular_items()
        if popular:
            return ChatResp(reply="I’m temporarily offline. Popular dishes: " + ", ".join(popular[:10]) + ".")
        raise HTTPException(status_code=503, detail="Assistant temporarily unavailable")

    # ---- Call OpenAI (NO 'timeout=' kwarg here) ----
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
    except (APIConnectionError, RateLimitError, APIStatusError) as e:
        log.error("openai error req_id=%s type=%s msg=%s", req_id, type(e).__name__, str(e))
        popular = get_popular_items()
        if popular:
            return ChatResp(reply="I’m having trouble reaching the assistant. Popular dishes: " + ", ".join(popular[:10]) + ".")
        raise HTTPException(status_code=502, detail="Chat service upstream error")
    except Exception as e:
        log.exception("openai unexpected req_id=%s err=%s", req_id, e)
        popular = get_popular_items()
        if popular:
            return ChatResp(reply="I’m having trouble reaching the assistant. Popular dishes: " + ", ".join(popular[:10]) + ".")
        raise HTTPException(status_code=502, detail="Chat service upstream error")

    # ---- Persist memory (best-effort) ----
    if memory and user_id:
        try:
            memory.add(user_msg, user_id=user_id, metadata={"app_id": "tomato", "role": "user"})
            memory.add(answer,   user_id=user_id, metadata={"app_id": "tomato", "role": "assistant"})
        except Exception:
            pass

    return ChatResp(reply=answer if isinstance(answer, str) else "")