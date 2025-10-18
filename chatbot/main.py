import os
import re
import hmac
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from itertools import islice

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from openai import OpenAI, APIConnectionError, RateLimitError, APIStatusError
from dotenv import load_dotenv
from difflib import SequenceMatcher  # fuzzy name match

# === ORDERING imports ===
import requests
from dataclasses import dataclass

try:
    from bson import ObjectId
except Exception:
    ObjectId = None

# ---------------- Env & Logging ----------------
load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tomatoai")

OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT  = float(os.getenv("OPENAI_TIMEOUT", "10"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "300"))

SHARED_SECRET   = os.getenv("SHARED_SECRET", "dev-secret")

MONGO_URI       = os.getenv("MONGO_URI")
DB_NAME         = os.getenv("DB_NAME", "food-delivery")

USE_MEMORY      = os.getenv("USE_MEMORY", "0") == "1"
FORCE_LLM       = os.getenv("FORCE_LLM", "0") == "1"

FRONTEND_ORIGINS = [o.strip() for o in (os.getenv("ALLOWED_ORIGINS") or "").split(",") if o.strip()]
if not FRONTEND_ORIGINS:
    FRONTEND_ORIGINS = ["*"]

# === ORDERING env ===
ORDER_API_BASE = (os.getenv("ORDER_API_BASE") or "").rstrip("/")
USER_JWT_HEADER = os.getenv("USER_JWT_HEADER", "X-User-JWT")
USER_COOKIE_HEADER = os.getenv("USER_COOKIE_HEADER", "X-User-Cookie")
REQUIRE_AUTH_FOR_ORDER = os.getenv("REQUIRE_AUTH_FOR_ORDER", "1") == "1"

# OpenAI client (safe init)
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
    if "decline_code" in t or "card_declined" in t or "declined" in t:
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
    found_types, found_codes, found_declines, found_statuses = [], [], [], []

    for k in STRIPE_ERRORS["types"]:
        if k in low: found_types.append(k)
    for k in STRIPE_ERRORS["codes"]:
        if k in low: found_codes.append(k)
    for k in STRIPE_ERRORS["decline_codes"]:
        if k in low: found_declines.append(k)
    for k in STRIPE_ERRORS["intents"]:
        if k in low: found_statuses.append(k)

    for m in re.finditer(r'(type|code|decline_code)\s*["\':]\s*["\']?([a-zA-Z0-9_\-]+)', t):
        field = _n(m.group(1)); val = _n(m.group(2))
        if field == "type" and val in STRIPE_ERRORS["types"] and val not in found_types: found_types.append(val)
        elif field == "code" and val in STRIPE_ERRORS["codes"] and val not in found_codes: found_codes.append(val)
        elif field == "decline_code" and val in STRIPE_ERRORS["decline_codes"] and val not in found_declines: found_declines.append(val)

    lines: List[str] = []
    if ("card_declined" in found_codes or "card_error" in found_types) and found_declines:
        dc = found_declines[0]; lines.append(f"Stripe says **card_declined / {dc}** — {STRIPE_ERRORS['decline_codes'][dc]}")
    if found_codes and not lines:
        c = found_codes[0]; lines.append(f"Stripe **{c}** — {STRIPE_ERRORS['codes'][c]}")
    if found_types and not lines:
        ty = found_types[0]; lines.append(f"Stripe **{ty}** — {STRIPE_ERRORS['types'][ty]}")
    if found_statuses:
        st = found_statuses[0]; lines.append(f"Status **{st}** — {STRIPE_ERRORS['intents'][st]}")

    if not lines:
        lines = ["I can help with Stripe errors. If you paste the JSON (type / code / decline_code), I’ll decode it."]
    lines.append("Next steps: confirm the exact error fields, retry only idempotently, or collect a new payment method / contact bank if it’s a decline.")
    reply = " ".join(lines)
    return (reply[:900].rstrip() + "…") if len(reply) > 900 else reply

# ---------------- Seed Data ----------------
STATIC_FOODS = [
    {"name": "Greek salad", "category": "salad", "price": 12, "description": "Classic Mediterranean salad; not spicy. Ingredients: cucumber, ripe tomatoes, red onion, Kalamata olives, feta, oregano, olive oil & lemon."},
    {"name": "Veg salad", "category": "salad", "price": 18, "description": "Crisp garden salad; not spicy. Ingredients: mixed greens, cucumber, tomato, carrots, sweet corn, bell peppers, light lemon-herb vinaigrette."},
    {"name": "Clover Salad", "category": "salad", "price": 16, "description": "Wholesome green bowl; not spicy. Ingredients: lettuce, chickpeas, cucumber, cherry tomatoes, fresh herbs, avocado, lemon-tahini dressing."},
    {"name": "Chicken Salad", "category": "salad", "price": 24, "description": "Protein-packed salad; not spicy. Ingredients: grilled chicken, lettuce, celery, cherry tomatoes, cucumber, yogurt-mayo dressing, parsley."},
    {"name": "Lasagna Rolls", "category": "rolls", "price": 14, "description": "Baked pasta roll-ups; not spicy. Ingredients: lasagna sheets, ricotta, spinach, mozzarella, marinara sauce, parmesan."},
    {"name": "Peri Peri Rolls", "category": "rolls", "price": 12, "description": "Fiery wrap; spicy. Ingredients: peri-peri marinated chicken, onions, lettuce, pickles, creamy chili mayo, soft roll/flatbread."},
    {"name": "Chicken Rolls", "category": "rolls", "price": 20, "description": "Street-style kathi roll; medium spicy. Ingredients: spiced chicken, sautéed onions, capsicum, lime, coriander, egg-paratha or tortilla."},
    {"name": "Veg Rolls", "category": "rolls", "price": 15, "description": "Hearty veggie wrap; mild. Ingredients: paneer/potato & mixed veg, onions, capsicum, mint-yogurt or chutney, wrapped in paratha/tortilla."},
    {"name": "Ripple Ice Cream", "category": "desserts", "price": 14, "description": "Creamy ice cream streaked with sauce; sweet. Ingredients: dairy base, fudge/caramel ‘ripple’, vanilla."},
    {"name": "Fruit Ice Cream", "category": "desserts", "price": 22, "description": "Real fruit in every scoop; sweet. Ingredients: dairy base, seasonal fruit purée (e.g., strawberry/mango), fruit chunks."},
    {"name": "Jar Ice Cream", "category": "desserts", "price": 10, "description": "Layered jar dessert; sweet. Ingredients: ice cream, crushed cookies/cake, sauce (chocolate/caramel), whipped cream."},
    {"name": "Vanilla Ice Cream", "category": "desserts", "price": 12, "description": "Classic vanilla; sweet. Ingredients: dairy base, Madagascar/Bourbon vanilla."},
    {"name": "Chicken Sandwich", "category": "sandwich", "price": 12, "description": "Comfort sandwich; not spicy. Ingredients: grilled or crispy chicken, lettuce, tomato, pickles, mayo in toasted bread/bun."},
    {"name": "Vegan Sandwich", "category": "sandwich", "price": 18, "description": "Plant-based; mild. Ingredients: hummus or avocado, grilled vegetables, cucumber, tomato, greens, olive oil on whole-grain bread."},
    {"name": "Grilled Sandwich", "category": "sandwich", "price": 16, "description": "Golden & melty; not spicy. Ingredients: butter-toasted bread, cheese, tomato, onion, herbs."},
    {"name": "Bread Sandwich", "category": "sandwich", "price": 24, "description": "Simple veggie sandwich; mild. Ingredients: soft bread, cucumber, tomato, lettuce, cheese (optional), butter/mayo."},
    {"name": "Cup Cake", "category": "cake", "price": 14, "description": "Single-serve frosted cake; sweet. Ingredients: flour, butter, sugar, eggs, vanilla/cocoa, buttercream frosting."},
    {"name": "Vegan Cake", "category": "cake", "price": 12, "description": "Egg- & dairy-free; sweet. Ingredients: flour, cocoa/vanilla, plant milk, vegetable oil, sugar, vegan frosting."},
    {"name": "Butterscotch Cake", "category": "cake", "price": 20, "description": "Rich caramel notes; sweet. Ingredients: vanilla sponge, butterscotch sauce, praline/toffee bits, whipped cream."},
    {"name": "Sliced Cake", "category": "cake", "price": 15, "description": "Classic tea-time slice; sweet. Ingredients: butter pound cake/vanilla sponge, light glaze (optional)."},
    {"name": "Garlic Mushroom", "category": "veg", "price": 14, "description": "Savory sauté; not spicy. Ingredients: button mushrooms, garlic, butter/olive oil, parsley, black pepper."},
    {"name": "Fried Cauliflower", "category": "veg", "price": 22, "description": "Crispy florets; mild. Ingredients: cauliflower, seasoned batter, oil for frying, optional garlic/pepper sprinkle."},
    {"name": "Mix Veg Pulao", "category": "veg", "price": 10, "description": "Fragrant rice; mildly spiced. Ingredients: basmati rice, peas, carrots, beans, onions, whole spices (cumin, bay, clove), ghee/oil."},
    {"name": "Rice Zucchini", "category": "veg", "price": 12, "description": "Light herby rice; not spicy. Ingredients: rice, sautéed zucchini, garlic, olive oil, parsley, lemon zest."},
    {"name": "Cheese Pasta", "category": "pasta", "price": 12, "description": "Comfort cheesy pasta; not spicy. Ingredients: pasta, cheddar/mozzarella, milk/cream, butter, garlic."},
    {"name": "Tomato Pasta", "category": "pasta", "price": 18, "description": "Bright marinara; mild. Ingredients: pasta, tomato sauce, garlic, olive oil, basil, parmesan."},
    {"name": "Creamy Pasta", "category": "pasta", "price": 16, "description": "Silky white-sauce pasta; not spicy. Ingredients: pasta, cream or béchamel, garlic, parmesan, black pepper."},
    {"name": "Chicken Pasta", "category": "pasta", "price": 24, "description": "Hearty & satisfying; mild. Ingredients: pasta, grilled chicken, tomato or cream sauce, garlic, herbs, parmesan."},
    {"name": "Butter Noodles", "category": "noodles", "price": 14, "description": "Simple & comforting; not spicy. Ingredients: noodles, butter, garlic (optional), herbs, salt & pepper."},
    {"name": "Veg Noodles", "category": "noodles", "price": 12, "description": "Stir-fried chow mein; mild to medium. Ingredients: wheat noodles, cabbage, carrot, bell pepper, spring onion, soy-garlic sauce."},
    {"name": "Somen Noodles", "category": "noodles", "price": 20, "description": "Japanese thin noodles; not spicy. Ingredients: chilled somen, light soy-dashi dipping sauce, scallions, sesame."},
    {"name": "Cooked Noodles", "category": "noodles", "price": 15, "description": "House stir-fried noodles; mild. Ingredients: boiled noodles, mixed vegetables, soy sauce, garlic, a touch of sesame oil."},
]

def bootstrap_foods_if_empty():
    if db is None:
        return
    try:
        for item in STATIC_FOODS:
            doc = {
                "name": item["name"].strip(),
                "category": item["category"].strip().lower(),
                "price": float(item["price"]),
                "orders": POPULARITY_START,
                "description": item.get("description", "").strip(),
            }
            db["foods"].update_one({"name": doc["name"]}, {"$set": doc}, upsert=True)

        try:
            db["foods"].create_index([("name", 1)], unique=True)
            db["foods"].create_index([("category", 1)])
            db["foods"].create_index([("orders", -1)])
            db["foods"].create_index([("description", "text")])
        except Exception:
            pass

        log.info("Bootstrapped/updated foods (%d docs).", db["foods"].count_documents({}))
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

def to_safe_dt(v):
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

# menu helpers
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
        if float(p).is_integer():
            return f"${int(float(p))}"
        return f"${float(p)}"
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

# ---- Item-detail helpers ----
def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()

def list_all_food_names(limit=200) -> List[str]:
    if db is None:
        return []
    try:
        cur = db["foods"].find({}, {"name": 1}).limit(limit)
        return [d["name"] for d in cur if d.get("name")]
    except Exception:
        return []

def find_item_candidates_by_name(query: str, limit: int = 5):
    if db is None:
        return []
    q = _norm(query)
    if not q:
        return []
    try:
        proj = {"_id": 1, "name": 1, "price": 1, "category": 1, "description": 1}

        exact = list(db["foods"].find(
            {"name": {"$regex": f"^{re.escape(query)}$", "$options": "i"}}, proj
        ).limit(1))
        if exact:
            return exact

        contains = list(db["foods"].find(
            {"name": {"$regex": re.escape(query), "$options": "i"}}, proj
        ).limit(limit * 3))

        pool = contains or list(db["foods"].find({}, proj))

        ranked = []
        for doc in pool:
            name = doc.get("name") or ""
            desc = doc.get("description") or ""
            score = max(_similar(query, name), _similar(query, desc[:60]))
            ranked.append((score, doc))
        ranked.sort(key=lambda t: t[0], reverse=True)
        return [d for (score, d) in ranked[:limit] if score >= 0.55]
    except Exception:
        log.exception("find_item_candidates_by_name failed")
        return []

def format_item_detail(item: dict) -> str:
    name = item.get("name", "Item")
    price = item.get("price")
    cat = (item.get("category") or "").rstrip("s")
    desc = (item.get("description") or "").strip()
    try:
        price_str = f"${int(float(price))}" if float(price).is_integer() else f"${float(price)}"
    except Exception:
        price_str = f"${price}" if price is not None else ""
    bits = [f"{name} ({price_str})"]
    if cat:
        bits.append(f"– {cat}")
    if desc:
        bits.append(f": {desc}")
    return " ".join(bits)

# ---- User/Orders helpers ----
def _possible_user_id_filters(user_id: str):
    if not user_id:
        return None
    candidates = [{"user_id": user_id}, {"userId": user_id}, {"user": user_id}]
    if ObjectId:
        try:
            oid = ObjectId(user_id)
            candidates.extend([{"user_id": oid}, {"userId": oid}, {"user": oid}])
        except Exception:
            pass
    candidates.extend([{"user.id": user_id}, {"user._id": user_id}])
    if ObjectId:
        try:
            oid = ObjectId(user_id)
            candidates.extend([{"user.id": oid}, {"user._id": oid}])
        except Exception:
            pass
    return {"$or": candidates}

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
        flt = _possible_user_id_filters(user_id)
        if not flt:
            return []
        cur = (
            db["orders"]
            .find(flt, {"order_id": 1, "_id": 1})
            .sort("order_date", -1)
            .limit(limit)
        )
        out: List[str] = []
        for doc in cur:
            oid = doc.get("order_id", doc.get("_id"))
            if oid is not None:
                out.append(str(oid))
        return out
    except PyMongoError:
        log.exception("get_user_recent_orders failed")
        return []

def _short_id(oid):
    s = str(oid or "")
    return ("…" + s[-2:]) if len(s) > 2 else s

def _fmt_date(d):
    dt = to_safe_dt(d)
    return dt.strftime("%b %d, %Y") if dt else "unknown date"

def _summarize_items(items, max_items=3):
    parts = []
    total_qty = 0
    items = items or []
    for it in items:
        name = (it.get("name") or "").strip()
        qty = int(it.get("qty") or 1)
        total_qty += qty
        if len(parts) < max_items and name:
            parts.append(f"{name} ×{qty}")
    more = max(0, len(items) - len(parts))
    preview = ", ".join(parts) + (f", +{more} more" if more > 0 else "")
    return total_qty, preview

def get_user_recent_orders_detailed(user_id: Optional[str], limit=MAX_RECENT):
    if db is None or not user_id or user_id == "guest":
        return []
    try:
        flt = _possible_user_id_filters(user_id)
        if not flt:
            return []
        pipeline = [
            {"$match": flt},
            {
                "$project": {
                    "_id": 1,
                    "order_id": 1,
                    "items": {"$ifNull": ["$items", []]},
                    "amount": 1,
                    "status": 1,
                    "dt": {"$ifNull": ["$date", {"$ifNull": ["$order_date", "$created_at"]}]},
                }
            },
            {"$sort": {"dt": -1, "_id": -1}},
            {"$limit": int(limit)},
        ]
        docs = list(db["orders"].aggregate(pipeline))
        out = []
        for d in docs:
            out.append({
                "order_id": d.get("order_id", d.get("_id")),
                "dt": d.get("dt"),
                "items": d.get("items") or [],
                "amount": d.get("amount"),
                "status": d.get("status"),
            })
        return out
    except Exception:
        log.exception("get_user_recent_orders_detailed failed")
        return []

def _items_from_cart_map(cart_map: dict):
    """Convert {<foodId>: qty} map into [{name, qty}] using foods collection.
       Ignore zero/negative quantities."""
    if not isinstance(cart_map, dict) or not cart_map or db is None:
        return []
    ids = []
    if ObjectId:
        for k in cart_map.keys():
            try:
                ids.append(ObjectId(k))
            except Exception:
                pass
    name_by_id: Dict[str, str] = {}
    try:
        if ids:
            cur = db["foods"].find({"_id": {"$in": ids}}, {"_id": 1, "name": 1})
            for d in cur:
                name_by_id[str(d["_id"])] = d.get("name") or str(d["_id"])
    except Exception:
        pass

    items = []
    for k, v in cart_map.items():
        try:
            qty = int(v)
        except Exception:
            qty = 0
        if qty <= 0:
            continue  # <-- skip zeros (removed items)
        name = name_by_id.get(str(k), str(k))
        items.append({"name": name, "qty": qty})
    return items

# -------- Last payment / transaction helpers --------
_PAYMENT_STATUS_KEYWORDS = (
    "why my previous transaction got failed",
    "why did my previous transaction fail",
    "why did my payment fail",
    "payment failed",
    "transaction failed",
    "last payment",
    "previous payment",
    "previous transaction",
    "payment status",
    "was my payment successful",
    "did my payment go through",
    "stripe failure",
    "declined payment",
    "3ds failed",
)

def is_payment_status_query(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in _PAYMENT_STATUS_KEYWORDS)

def _pick_dt(d):
    return d.get("date") or d.get("order_date") or d.get("created_at")

def get_user_last_order_with_payment(user_id: Optional[str]) -> Optional[dict]:
    if db is None or not user_id or user_id == "guest":
        return None
    try:
        flt = _possible_user_id_filters(user_id)
        if not flt:
            return None
        pipeline = [
            {"$match": flt},
            {"$addFields": {"_dt": {"$ifNull": ["$date", {"$ifNull": ["$order_date", "$created_at"]}]}}},
            {"$sort": {"_dt": -1, "_id": -1}},
            {"$limit": 1},
            {"$project": {
                "_id": 1,
                "order_id": 1,
                "items": 1,
                "amount": 1,
                "status": 1,
                "payment": 1,
                "paymentInfo": 1,
                "date": 1,
                "order_date": 1,
                "created_at": 1,
            }},
        ]
        rows = list(db["orders"].aggregate(pipeline))
        return rows[0] if rows else None
    except Exception:
        log.exception("get_user_last_order_with_payment failed")
        return None

def explain_last_order_payment(order: dict) -> str:
    if not order:
        return "I couldn’t find a previous transaction for your account yet."

    amt = order.get("amount")
    dt_raw = _pick_dt(order) or ((order.get("paymentInfo", {}) or {}).get("stripe", {}) or {}).get("paidAt")
    dt = to_safe_dt(dt_raw)
    when = dt.strftime("%b %d, %Y %H:%M %Z") if dt else "unknown time"

    paid_flag = bool(order.get("payment"))
    status = (order.get("status") or "").strip().upper()

    stripe = (order.get("paymentInfo", {}) or {}).get("stripe", {}) or {}
    stripe_status = (stripe.get("status") or "").strip().lower()
    err_code = (stripe.get("errorCode") or "").strip()
    err_msg = (stripe.get("errorMessage") or "").strip()
    sess_id = (stripe.get("sessionId") or "").strip()
    intent_id = (stripe.get("paymentIntentId") or "").strip()

    is_success = (
        paid_flag
        or status in ("PAID", "COMPLETED", "FULFILLED")
        or stripe_status == "succeeded"
    )

    if is_success:
        parts = [f"Your last payment was successful"]
        if amt is not None:
            try:
                parts.append(f"for ${int(float(amt)) if float(amt).is_integer() else float(amt)}")
            except Exception:
                parts.append(f"for ${amt}")
        parts.append(f"on {when}.")
        tail_bits = []
        if intent_id:
            tail_bits.append(f"intent {intent_id[:8]}…")
        if sess_id:
            tail_bits.append(f"session {sess_id[:8]}…")
        if tail_bits:
            parts.append("(" + ", ".join(tail_bits) + ")")
        return " ".join(parts)

    probe = []
    if err_code: probe.append(f"code: {err_code}")
    if err_msg:  probe.append(err_msg)
    if stripe_status: probe.append(f"status: {stripe_status}")
    if not probe and status:
        probe.append(f"status: {status.lower()}")

    if not probe:
        return ("It looks like the last payment did not succeed. "
                "Please try another card or contact your bank, and you can try again.")
    return explain_stripe_error(" | ".join(probe))

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
                            {"$match": {"$expr": {"$and": [
                                {"$eq": [{"$toLower": "$name"}, {"$toLower": "$$itemName"}]},
                                {"$eq": [{"$toLower": "$category"}, cat_lower]},
                            ]}}}
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
            name = (it.get("name") or it.get("item_id") or "").strip()
            qty = int(it.get("qty") or 1)
            if not name:
                continue
            db["foods"].update_one({"name": name}, {"$inc": {"orders": qty}})
    except Exception:
        log.exception("bump_food_orders failed")

# ---------------- LLM helpers ----------------
SYSTEM_PROMPT = (
    "You are TomatoAI, a concise, friendly customer support chatbot for a food delivery platform. "
    "Only use details present in the Database context (Menu & Descriptions). "
    "Never invent items, ingredients, prices, policies, or order numbers. Keep replies short.\n"
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
            temperature=0.2,
            max_tokens=OPENAI_MAX_TOKENS,
        )
        out = (r.choices[0].message.content or "").strip()
        return out or content
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
    menu_names = list_all_food_names()

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
    if menu_names: ctx_parts.append("Menu (names only): " + ", ".join(menu_names))

    return ("Database context:\n" + "\n".join(ctx_parts) + "\n") if ctx_parts else ""

def guarded_rewrite(user_msg: str, draft: str) -> str:
    menu = ", ".join(list_all_food_names())
    rules = (
        "Rules:\n"
        "• ONLY reference items that appear in Menu.\n"
        "• If the user mentions an item not in Menu, say it isn't on our menu and suggest the closest 1–3 matches by name only.\n"
        "• Do not invent ingredients, prices, sizes, or availability beyond the provided Descriptions.\n"
        "• Keep it under ~80 words unless the customer explicitly asks for more.\n"
    )
    content = f"{rules}\nMenu: {menu}\n\nCustomer: {user_msg}\nDraft: {draft}\nRewrite the Draft to answer the Customer. Stay faithful to the Draft."
    return llm_compose(SYSTEM_PROMPT, content)

# ---------------- Previous Orders intent ----------------
_PREV_ORDERS_KEYWORDS = (
    "previous orders", "past orders", "order history", "my orders",
    "recent orders", "last order", "my last order", "history of orders",
    "show my orders", "show my previous orders", "show my recent orders"
)
def is_previous_orders_query(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in _PREV_ORDERS_KEYWORDS)

# === ORDER CLIENT & MODELS ===
@dataclass
class AddToCartPayload:
    item_id: str
    qty: int = 1
    modifiers: Optional[List[Dict[str, Any]]] = None

@dataclass
class CheckoutPayload:
    address: Dict[str, Any]
    contact: Dict[str, Any]
    method: str = "card"  # "card" | "cash"

@dataclass
class ConfirmPayload:
    payment_intent_id: str

class OrderClient:
    def __init__(self, base_url: str, jwt: str = "", cookie: str = ""):
        if not base_url:
            raise RuntimeError("ORDER_API_BASE not configured")
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json"})

        # Forward auth in all the common places
        if jwt:
            self.s.headers[USER_JWT_HEADER] = jwt
            self.s.headers["Authorization"] = f"Bearer {jwt}"
            self.s.headers["token"] = jwt
        if cookie:
            self.s.headers["Cookie"] = cookie
            self.s.headers[USER_COOKIE_HEADER] = cookie

    def _url(self, p: str) -> str:
        return f"{self.base}{p}"

    def _safe_json(self, r: requests.Response):
        try:
            return r.json()
        except Exception:
            return {"raw": (r.text or "")[:500], "status": r.status_code}

    def _post_try(self, candidates: List[str], json: dict, timeout: float = 8.0):
        last_err = None
        for p in candidates:
            try:
                r = self.s.post(self._url(p), json=json, timeout=timeout)
                data = self._safe_json(r)
                if 200 <= r.status_code < 300:
                    return data
                last_err = f"{p} -> {r.status_code} {(r.text or '')[:200]}"
            except Exception as e:
                last_err = f"{p} -> {e}"
        raise RuntimeError(f"POST failed for {candidates}: {last_err}")

    def _get_try(self, candidates: List[str], timeout: float = 8.0):
        last_err = None
        for p in candidates:
            try:
                r = self.s.get(self._url(p), timeout=timeout)
                data = self._safe_json(r)
                if 200 <= r.status_code < 300:
                    return data
                last_err = f"{p} -> {r.status_code} {(r.text or '')[:200]}"
            except Exception as e:
                last_err = f"{p} -> {e}"
        raise RuntimeError(f"GET failed for {candidates}: {last_err}")

    # ---- CART ----
    def add_to_cart(self, payload: AddToCartPayload, user_id: Optional[str] = None):
        body = {"itemId": payload.item_id, "qty": payload.qty, "modifiers": payload.modifiers or []}
        if user_id:
            body["userId"] = user_id
        return self._post_try(
            candidates=["/cart/add", "/cart/items", "/cart"],
            json=body,
            timeout=6.0,
        )

    def get_cart(self, user_id: Optional[str] = None):
        try:
            if user_id:
                return self._get_try(
                    candidates=[
                        f"/cart/get?userId={user_id}",
                        f"/cart?userId={user_id}",
                        f"/cart/items?userId={user_id}",
                        "/cart/get",
                        "/cart/items",
                        "/cart",
                    ],
                    timeout=6.0,
                )
            return self._get_try(
                candidates=["/cart/get", "/cart/items", "/cart"],
                timeout=6.0,
            )
        except Exception:
            return self._post_try(candidates=["/cart/get"], json={}, timeout=6.0)

    def checkout(self, payload: CheckoutPayload):
        body = {
            "address": payload.address,
            "contact": payload.contact,
            "method": payload.method,
        }
        return self._post_try(
            candidates=["/order/place", "/order/checkout-session", "/order/checkout", "/order"],
            json=body,
            timeout=10.0,
        )

    def confirm(self, payload: ConfirmPayload):
        return self._post_try(
            candidates=["/order/confirm", "/order/complete", "/order/finalize"],
            json={"paymentIntentId": payload.payment_intent_id},
            timeout=8.0,
        )

    def remove_from_cart(self, item_id: str, qty: int = 1, user_id: Optional[str] = None):
        """Remove qty of an item from cart. Tries Node /cart/remove first, then fallbacks."""
        body = {"itemId": item_id, "qty": max(1, int(qty))}
        if user_id:
            body["userId"] = user_id
        return self._post_try(
            candidates=["/cart/remove", "/cart/items/remove"],
            json=body,
            timeout=6.0,
        )

# === ORDERING intent extraction (multi-item support) ===
SHOW_CART_PATTERNS = (
    r"\b(show|view|see)\b.*\b(cart|basket|bag)\b",
    r"\bwhat'?s in (my )?cart\b",
    r"\bshowcart\b",
    r"\bviewcart\b",
    r"\bmy\s*cart\b",
    r"\bcart\b",
    r"\bbasket\b",
    r"\bbag\b",
)

CHECKOUT_PATTERNS = (r"\b(check ?out|pay|proceed to payment|place (the )?order)\b",)
CONFIRM_PATTERNS = (r"\b(confirm|finalize)\b.*\b(payment|order)\b",)
ADD_PATTERNS = (r"\b(add|order|get|i'?ll have|i want)\b",)
REMOVE_PATTERNS = (
    r"\b(remove|delete|take\s*out|subtract)\b",
)

ITEM_QTY_PATTERN = re.compile(
    r"""
    (?P<name>[A-Za-z][A-Za-z\s\-]+?)       # item name
    (?:\s*[xX\*]\s*(?P<qty>\d{1,3}))?      # optional x 2 / * 2
    (?=\s*(?:,|\n|and\b|&\b|$))            # stop at separators
    """,
    re.VERBOSE | re.IGNORECASE
)

def parse_items_with_qty(text: str):
    out = []
    if not text:
        return out
    parts = re.split(r"(?:,|\n| and | & )", text, flags=re.IGNORECASE)
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = ITEM_QTY_PATTERN.search(p)
        if not m:
            continue
        name = (m.group("name") or "").strip(" -")
        qty = int(m.group("qty") or 1)
        if name:
            out.append({"name": name, "qty": max(1, qty)})
    return out

def map_to_menu_items(requested: list):
    results = []
    for r in requested:
        cands = find_item_candidates_by_name(r["name"], limit=1)
        if cands:
            _id = str(cands[0].get("_id") or "")
            nm  = cands[0].get("name") or r["name"]
            if _id:
                results.append({"item_id": _id, "display": nm, "qty": r["qty"]})
    return results

def extract_payment_method(text: str) -> str:
    t = (text or "").lower()
    if "cash" in t: return "cash"
    return "card"

def extract_address_and_contact_from_mem(user_id: Optional[str]):
    addr = {"line1": "", "city": "", "zip": ""}
    contact = {"name": "", "phone": "", "email": ""}
    if memory and user_id:
        try:
            results = memory.search(query="delivery address and contact", user_id=user_id) or {}
            for m in take(results.get("results", []), 5):
                s = (m.get("memory") or "")
                low = s.lower()
                if any(k in low for k in ("street", "st.", "ave", "apt", "zip")) and not addr["line1"]:
                    addr["line1"] = s[:120]
                if "@" in s and not contact["email"]:
                    contact["email"] = s.strip()[:120]
                phone_match = re.search(r"\b\d{7,}\b", low)
                if phone_match and not contact["phone"]:
                    contact["phone"] = phone_match.group(0)
        except Exception:
            pass
    return addr, contact

def _foods_name_map_by_ids(ids: List[str]) -> Dict[str, str]:
    """Return {id -> name} for foods `_id`s."""
    out: Dict[str, str] = {}
    if not ids or db is None:
        return out
    q_ids = []
    if ObjectId:
        for sid in ids:
            try:
                q_ids.append(ObjectId(sid))
            except Exception:
                pass
    try:
        if q_ids:
            cur = db["foods"].find({"_id": {"$in": q_ids}}, {"_id": 1, "name": 1})
            for d in cur:
                out[str(d["_id"])] = d.get("name") or str(d["_id"])
    except Exception:
        pass
    return out

def _normalize_cart_items(payload: dict) -> List[Dict[str, Any]]:
    """
    Accepts any of your backend shapes and returns a list:
    [{ "id": "<foodId>", "name": "<display name>", "qty": <int> }, ...]
    """
    items = (
        (payload or {}).get("items")
        or (payload or {}).get("cart", {}).get("items")
        or (payload or {}).get("data", {}).get("items")
        or (payload or {}).get("result", {}).get("items")
        or []
    )

    # Node template shape: { cartData: { "<foodId>": qty } }
    if not items:
        cart_map = (
            (payload or {}).get("cartData")
            or (payload or {}).get("data", {}).get("cartData")
            or {}
        )
        if isinstance(cart_map, dict) and cart_map:
            ids = list(cart_map.keys())
            name_map = _foods_name_map_by_ids(ids)
            out = []
            for sid, q in cart_map.items():
                out.append({"id": sid, "name": name_map.get(sid, sid), "qty": int(q or 1)})
            return out

    out = []
    ids_missing_names = []
    for it in items:
        iid = str(it.get("_id") or it.get("itemId") or it.get("id") or "").strip()
        nm  = (it.get("name") or it.get("title") or it.get("product") or "").strip()
        q   = int(it.get("qty") or it.get("quantity") or 1)
        if not nm and iid:
            ids_missing_names.append(iid)
        out.append({"id": iid, "name": nm, "qty": q})

    if ids_missing_names:
        name_map = _foods_name_map_by_ids(ids_missing_names)
        for it in out:
            if not it["name"] and it["id"]:
                it["name"] = name_map.get(it["id"], it["id"])
    return out

def _index_cart_by_name(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Case-insensitive index by display name."""
    idx: Dict[str, Dict[str, Any]] = {}
    for it in items:
        key = (it.get("name") or "").strip().lower()
        if key:
            idx[key] = it
    return idx

def extract_action(user_msg: str) -> Optional[dict]:
    t = (user_msg or "").strip()
    low = t.lower()

    for p in SHOW_CART_PATTERNS:
        if re.search(p, low, flags=re.IGNORECASE):
            return {"type": "show_cart", "slots": {}}

    if any(re.search(p, low) for p in CHECKOUT_PATTERNS):
        return {"type": "checkout", "slots": {"payment_method": extract_payment_method(low)}}

    if any(re.search(p, low) for p in CONFIRM_PATTERNS):
        pid = None
        m = re.search(r"pi_[A-Za-z0-9_]+", t)
        if m: pid = m.group(0)
        return {"type": "confirm_order", "slots": {"payment_intent_id": pid}}

    if any(re.search(p, low) for p in ADD_PATTERNS):
        items_req = parse_items_with_qty(t)
        if items_req:
            mapped = map_to_menu_items(items_req)
            if mapped:
                return {"type": "add_multiple", "slots": {"items": mapped}}
            return {"type": "disambiguate", "slots": {"choices": []}}
        return {"type": "prompt_for_items", "slots": {}}

    # REMOVE items intent
    if any(re.search(p, low) for p in REMOVE_PATTERNS):
        items_req = parse_items_with_qty(t)
        if items_req:
            mapped = map_to_menu_items(items_req)
            if mapped:
                return {"type": "remove_multiple", "slots": {"items": mapped, "original": items_req}}
            return {"type": "disambiguate", "slots": {"choices": []}}
        return {"type": "prompt_for_remove", "slots": {}}

    return None

# ---------------- API Models ----------------
class ChatReq(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MSG_LEN)
    userId: Optional[str] = Field(default=None, max_length=120)

class ChatResp(BaseModel):
    reply: str

# ---------------- App ----------------
app = FastAPI(title="Tomato Chatbot API", version="1.9.0-ordering")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        "version": "1.9.0-ordering",
        "force_llm": FORCE_LLM,
    }

@app.post("/whoami")
def whoami(req: ChatReq, x_service_auth: str = Header(default="")):
    if not safe_eq(x_service_auth, SHARED_SECRET):
        raise HTTPException(status_code=401, detail="Unauthorized")
    flt = _possible_user_id_filters(req.userId or "")
    return {"received_userId": req.userId or None, "filter": flt}

# ---------------- Chat ----------------
@app.post("/chat", response_model=ChatResp)
async def chat(req: ChatReq, x_service_auth: str = Header(default=""), request: Request = None, response: Response = None):
    if not safe_eq(x_service_auth, SHARED_SECRET):
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_msg = trim_text(req.message, MAX_MSG_LEN)
    if not user_msg:
        raise HTTPException(status_code=400, detail="message is required")

    user_id = req.userId or None
    if response is not None:
        response.headers["X-Echo-UserId"] = str(user_id or "")

    # ---- Last payment / previous transaction status ----
    if is_payment_status_query(user_msg):
        if not user_id or user_id == "guest":
            text = "Please log in to check your last transaction status. Once signed in, ask again."
            if response is not None:
                response.headers["X-Answer-Source"] = "rule:payment_login_required"
            return ChatResp(reply=text)

        last = get_user_last_order_with_payment(user_id)
        msg = explain_last_order_payment(last)
        if response is not None:
            response.headers["X-Answer-Source"] = "rule:payment_last_summary"
            if last and last.get("status"):
                response.headers["X-Last-Order-Status"] = str(last.get("status"))
        return ChatResp(reply=msg)

    if is_stripe_query(user_msg):
        reply = explain_stripe_error(user_msg)
        if response is not None:
            response.headers["X-Answer-Source"] = "rule:stripe"
        return ChatResp(reply=reply)

    # ---- Previous orders ----
    if is_previous_orders_query(user_msg):
        if not user_id or user_id == "guest":
            text = "To show your past orders, please log in. Once you’re signed in, ask “show my recent orders.”"
            if response is not None:
                response.headers["X-Answer-Source"] = "rule:orders_login_required"
            return ChatResp(reply=text)

        detailed = get_user_recent_orders_detailed(user_id, limit=MAX_RECENT)
        if detailed:
            lines = ["Your recent orders:"]
            for o in detailed:
                when = _fmt_date(o.get("dt") or o.get("order_date") or o.get("date") or o.get("created_at"))
                total_qty, preview = _summarize_items(o.get("items"), max_items=3)
                tail = f"(id {_short_id(o.get('order_id'))})" if o.get("order_id") is not None else ""
                lines.append(f"• {when} — {total_qty} items: {preview} {tail}".rstrip())
            text = "\n".join(lines)
            if response is not None:
                response.headers["X-Answer-Source"] = "rule:orders_list_detailed"
                response.headers["X-Orders-Count"] = str(len(detailed))
            return ChatResp(reply=text)

        text = "I couldn’t find past orders for your account yet. You can place an order and I’ll track it here."
        if response is not None:
            response.headers["X-Answer-Source"] = "rule:orders_none"
            response.headers["X-Orders-Count"] = "0"
        return ChatResp(reply=text)

    # === ORDERING (cart, checkout, confirm, remove) ===
    action = extract_action(user_msg)
    if action and ORDER_API_BASE:
        # --- collect auth from either custom headers or standard Authorization ---
        jwt_token = ""
        fwd_cookie = ""
        if request:
            jwt_token = request.headers.get(USER_JWT_HEADER, "") or ""
            auth_hdr = request.headers.get("authorization", "") or request.headers.get("Authorization", "") or ""
            if not jwt_token and auth_hdr:
                jwt_token = auth_hdr
            if jwt_token.lower().startswith("bearer "):
                jwt_token = jwt_token[7:].strip()
            fwd_cookie = request.headers.get(USER_COOKIE_HEADER, "") or ""

        if REQUIRE_AUTH_FOR_ORDER and not (jwt_token or fwd_cookie):
            txt = ("Please log in to your account, then try again from the same browser.")
            if response is not None:
                response.headers["X-Answer-Source"] = "order:auth_missing"
                response.headers["X-Debug-Auth-HasJWT"] = "0"
                response.headers["X-Debug-Auth-HasCookie"] = "0"
            return ChatResp(reply=txt)

        if response is not None:
            response.headers["X-Debug-Auth-HasJWT"] = "1" if jwt_token else "0"
            response.headers["X-Debug-Auth-HasCookie"] = "1" if (fwd_cookie or "").strip() else "0"

        try:
            oc = OrderClient(ORDER_API_BASE, jwt=jwt_token, cookie=fwd_cookie)
        except Exception as e:
            log.error("OrderClient init failed: %s", e)
            return ChatResp(reply="Ordering is temporarily unavailable. Please try again shortly.")

        t = action["type"]
        slots = action.get("slots", {})

        if t == "prompt_for_items":
            if response is not None:
                response.headers["X-Answer-Source"] = "order:prompt_items"
            return ChatResp(reply='Tell me what to add like: “Rice Zucchini x 1, Clover Salad x 2”.')

        if t == "add_multiple":
            added, failed = [], []
            for it in slots.get("items", []):
                try:
                    item_id = it.get("item_id") or it.get("name")
                    qty = int(it.get("qty", 1)) or 1
                    _ = oc.add_to_cart(AddToCartPayload(item_id=item_id, qty=qty, modifiers=[]), user_id=user_id)
                    bump_food_orders([{"item_id": it.get("display") or item_id, "qty": qty}])
                    added.append(f"{(it.get('display') or item_id)} ×{qty}")
                except Exception:
                    failed.append(it.get("display") or it.get("item_id") or it.get("name") or "item")
            if response is not None:
                response.headers["X-Answer-Source"] = "order:add_multi"
                response.headers["X-Cart-Should-Refresh"] = "1"
            if added and not failed:
                return ChatResp(reply=f"Added: {', '.join(added)}. Say “show cart” or “checkout”.")
            if added and failed:
                return ChatResp(reply=f"Added: {', '.join(added)}. Couldn’t add: {', '.join(failed)}. Say “show cart” or try again.")
            return ChatResp(reply="I couldn’t add those items. Please check the names and try again.")

        if t == "disambiguate":
            choices = ", ".join(slots.get("choices", [])[:5]) or "please share the exact item names"
            if response is not None:
                response.headers["X-Answer-Source"] = "order:item_disambiguate"
            return ChatResp(reply=f"Did you mean: {choices}? Tell me the exact names, e.g., “Veg Noodles x 1, Greek salad x 2”.")

        if t == "show_cart":
            try:
                cart = oc.get_cart(user_id=user_id)

                # 🔧 Be defensive about upstream shape
                if not isinstance(cart, dict):
                    log.warning("show_cart upstream not dict: %s", type(cart).__name__)
                    cart = {}

                payload = cart
                items = (
                    payload.get("items")
                    or (payload.get("cart") or {}).get("items")
                    or (payload.get("data") or {}).get("items")
                    or (payload.get("result") or {}).get("items")
                    or []
                )

                if not isinstance(items, list):
                    log.warning("show_cart 'items' not list, coercing empty; was: %r", items)
                    items = []

                # Handle Node template shape: { success, cartData: { "<foodId>": qty, ... } }
                if not items:
                    cart_map = (
                        payload.get("cartData")
                        or (payload.get("data") or {}).get("cartData")
                        or {}
                    )
                    if isinstance(cart_map, dict) and cart_map:
                        items = _items_from_cart_map(cart_map)
                    elif cart_map and not isinstance(cart_map, dict):
                        log.warning("show_cart cartData unexpected type: %s", type(cart_map).__name__)

                if not items:
                    if response is not None:
                        response.headers["X-Answer-Source"] = "order:cart_empty"
                        response.headers["X-Cart-Should-Refresh"] = "1"
                    return ChatResp(reply="Your cart is empty. Say “add Veg Noodles x 1”.")

                # Build a friendly preview
                parts = []
                for it in items[:5]:
                    it = it or {}
                    nm = (
                        it.get("name")
                        or it.get("itemId")
                        or it.get("title")
                        or it.get("product")
                        or "item"
                    )
                    nm = (nm or "item").strip() if isinstance(nm, str) else "item"
                    try:
                        q = int(it.get("qty") or it.get("quantity") or 1)
                    except Exception:
                        q = 1
                    parts.append(f"{nm} ×{q}")

                more = max(0, len(items) - len(parts))
                suffix = f" (+{more} more)" if more else ""

                if response is not None:
                    response.headers["X-Answer-Source"] = "order:show_cart"
                    response.headers["X-Cart-Should-Refresh"] = "1"

                return ChatResp(reply=f"In your cart: {', '.join(parts)}{suffix}. Say “checkout” to continue.")
            except Exception as e:
                if response is not None and request is not None:
                    response.headers["X-Answer-Source"] = "order:show_cart_error"
                    response.headers["X-Debug-Auth-HasJWT"] = "1" if request.headers.get(USER_JWT_HEADER) or request.headers.get("Authorization") else "0"
                    response.headers["X-Debug-Auth-HasCookie"] = "1" if (request.headers.get(USER_COOKIE_HEADER) or "").strip() else "0"
                log.exception("get_cart crashed (user_id=%s): %s", user_id, e)
                msg = "I couldn’t load your cart."
                if REQUIRE_AUTH_FOR_ORDER:
                    msg += " Please make sure you’re signed in and the app forwards your login to chat."
                return ChatResp(reply=msg)

        if t == "checkout":
            # Get user cart 
            cart = oc.get_cart(user_id)
            if not cart or not any(qty > 0 for qty in cart.values()):
                # Let OpenAI craft the response (not a static string)
                chat_prompt = (
                    "The customer tried to checkout, but their cart is empty. "
                    "Politely tell them the cart is empty and to choose some dishes before proceeding."
                )
                ai_msg = call_openai(chat_prompt)  # a helper that queries OpenAI (see below)
                return ChatResp(reply=ai_msg, answer_source="order:cart_empty")

            # Proceed to checkout if cart not empty
            addr, contact = extract_address_and_contact_from_mem(user_id)
            method = slots.get("payment_method", "card")
            try:
                res = oc.checkout(CheckoutPayload(address=addr, contact=contact, method=method))
                checkout_url = res.get("session_url") or res.get("checkoutUrl") or res.get("url") or ""
                client_secret = res.get("clientSecret") or ""

                if response is not None:
                    response.headers["X-Answer-Source"] = "order:checkout"
                    if checkout_url:
                        response.headers["X-Checkout-Url"] = checkout_url
                    if client_secret:
                        response.headers["X-Client-Secret"] = client_secret

                ui_hint = "Open the Cart at the top-right and click **Checkout** to complete your order."

                if checkout_url:
                    return ChatResp(reply=f"Secure payment link is ready. {ui_hint}")
                if client_secret:
                    return ChatResp(reply=f"Payment is ready in the app. {ui_hint}")

                return ChatResp(reply=f"Checkout is prepared. {ui_hint}")

            except Exception as e:
                log.error("checkout failed: %s", e)
                return ChatResp(reply="I couldn’t start checkout. Please verify your address and try again.")

        if t == "confirm_order":
            pid = slots.get("payment_intent_id")
            if not pid:
                return ChatResp(reply="If you have a Payment Intent ID, paste it and say “confirm payment”.")
            try:
                res = oc.confirm(ConfirmPayload(payment_intent_id=pid))
                order_id = res.get("orderId") or res.get("_id")
                eta = res.get("eta") or "soon"
                if response is not None:
                    response.headers["X-Answer-Source"] = "order:confirm"
                    if order_id:
                        response.headers["X-Order-Id"] = str(order_id)
                return ChatResp(reply=f"Order confirmed 🎉 ETA {eta}. I’ll keep you posted here.")
            except Exception as e:
                log.error("confirm failed: %s", e)
                return ChatResp(reply="I couldn’t confirm that payment. If it succeeded, you’ll see the order in your history shortly.")

        if t == "prompt_for_remove":
            if response is not None:
                response.headers["X-Answer-Source"] = "order:prompt_remove"
            return ChatResp(reply='Tell me what to remove like: “remove Veg Noodles x 1, Clover Salad x 2”.')

        if t == "remove_multiple":
            try:
                # Get current cart
                cart_payload = oc.get_cart(user_id=user_id)
                if not isinstance(cart_payload, dict):
                    cart_payload = {}
                current_items = _normalize_cart_items(cart_payload)
                index_by_name = _index_cart_by_name(current_items)

                removed_msgs = []
                not_in_cart  = []
                qty_too_high = []  # tuples of (name, wanted, have)

                for it in slots.get("items", []):
                    disp = (it.get("display") or "").strip()
                    want_qty = int(it.get("qty") or 1)
                    key = disp.lower()

                    current = index_by_name.get(key)
                    if not current:
                        not_in_cart.append(disp or "item")
                        continue

                    have_qty = int(current.get("qty") or 0)
                    if want_qty > have_qty:
                        qty_too_high.append((disp or "item", want_qty, have_qty))
                        continue

                    try:
                        oc.remove_from_cart(item_id=it.get("item_id"), qty=want_qty, user_id=user_id)
                        removed_msgs.append(f"{disp} ×{want_qty}")
                    except Exception:
                        qty_too_high.append((disp or "item", want_qty, have_qty))

                parts = []
                if removed_msgs:
                    parts.append("Removed: " + ", ".join(removed_msgs) + ".")
                if not_in_cart:
                    parts.append("Not in cart: " + ", ".join(not_in_cart) + ".")
                if qty_too_high:
                    err_bits = []
                    for nm, w, h in qty_too_high:
                        err_bits.append(f"{nm} (you asked {w}, only {h} in cart)")
                    parts.append("Too many to remove: " + ", ".join(err_bits) + ".")

                if not parts:
                    parts = ["I couldn’t remove those items. Please check the names and amounts and try again."]

                if response is not None:
                    response.headers["X-Answer-Source"] = "order:remove_multi"
                    response.headers["X-Cart-Should-Refresh"] = "1"

                return ChatResp(reply=" ".join(parts) + " Say “show cart” to refresh.")
            except Exception as e:
                log.error("remove_multiple failed for user_id=%s: %s", user_id, e)
                msg = "I couldn’t update your cart."
                if REQUIRE_AUTH_FOR_ORDER:
                    msg += " Please make sure you’re signed in and try again."
                return ChatResp(reply=msg)

    # ---- Popularity questions (guarded LLM) ----
    if is_popularity_query(user_msg):
        cat = category_from_query(user_msg)
        names = top_items_from_orders(limit=3, category=cat) or top_items_from_foods(limit=3, category=cat)
        if names:
            draft = (
                f"Our most-ordered {cat.rstrip('s')} right now: " + ", ".join(names) + "."
                if cat else
                "Top items customers are ordering: " + ", ".join(names) + "."
            )
            final = guarded_rewrite(user_msg, draft) if FORCE_LLM else draft
            if response is not None:
                response.headers["X-Answer-Source"] = "rule+llm:popularity" if FORCE_LLM else "rule:popularity"
            return ChatResp(reply=final)

    # ---- Item detail (try by name; guarded LLM) ----
    candidates = find_item_candidates_by_name(user_msg, limit=3)
    if candidates:
        if len(candidates) > 1 and _similar(user_msg, candidates[0].get("name", "")) < 0.88:
            choices = ", ".join(c.get("name") for c in candidates)
            text = f"Did you mean: {choices}? Tell me the exact name for details."
            if response is not None:
                response.headers["X-Answer-Source"] = "rule:item_disambiguate"
            return ChatResp(reply=text)

        item = candidates[0]
        draft = format_item_detail(item)
        final = guarded_rewrite(user_msg, draft) if FORCE_LLM else draft
        if response is not None:
            response.headers["X-Answer-Source"] = "rule+llm:item_detail" if FORCE_LLM else "rule:item_detail"
        return ChatResp(reply=final)

    # ---- Category-first answers (guarded LLM) ----
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
                final = guarded_rewrite(user_msg, draft) if FORCE_LLM else draft
                if response is not None:
                    response.headers["X-Answer-Source"] = "rule+llm:category" if FORCE_LLM else "rule:category"
                return ChatResp(reply=final)

    # ---- Generic fallback ----
    db_context = build_context(user_msg, user_id)
    draft = "How can I help with our menu, your order, or delivery?"
    content = f"{db_context}\nCustomer: {user_msg}\nDraft: {draft}\nFollow the rules above."
    answer = guarded_rewrite(user_msg, draft) if FORCE_LLM else llm_compose(SYSTEM_PROMPT, content)
    if response is not None:
        response.headers["X-Answer-Source"] = "llm" if FORCE_LLM else "llm:plain"

    # ---- Persist memory (best-effort) ----
    if memory and user_id:
        try:
            memory.add(user_msg, user_id=user_id, metadata={"app_id": "tomato", "role": "user"})
            memory.add(answer,   user_id=user_id, metadata={"app_id": "tomato", "role": "assistant"})
        except Exception:
            pass

    return ChatResp(reply=answer if isinstance(answer, str) else "")

# Accept trailing slash to avoid 307/308 redirects returning HTML
@app.post("/chat/", response_model=ChatResp, include_in_schema=False)
async def chat_trailing_slash(req: ChatReq, x_service_auth: str = Header(default=""), request: Request = None, response: Response = None):
    return await chat(req, x_service_auth, request, response)