import os
import re
import hmac
import logging
from typing import Optional, List
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
        lines = [
            "I can help with Stripe errors. If you paste the JSON (type / code / decline_code), I’ll decode it.",
        ]
    lines.append("Next steps: confirm the exact error fields, retry only idempotently, or collect a new payment method / contact bank if it’s a decline.")
    reply = " ".join(lines)
    return (reply[:900].rstrip() + "…") if len(reply) > 900 else reply

# ---------------- Seed Data (descriptions already precise) ----------------
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
    """Upsert a small menu so DB answers work immediately (now with descriptions)."""
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
    """Parse Mongo Date or ISO string to aware datetime; else None."""
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
    """Find likely item matches via exact, regex, and fuzzy ranking."""
    if db is None:
        return []
    q = _norm(query)
    if not q:
        return []
    try:
        # exact case-insensitive
        exact = list(db["foods"].find(
            {"name": {"$regex": f"^{re.escape(query)}$", "$options": "i"}},
            {"_id": 0, "name": 1, "price": 1, "category": 1, "description": 1}
        ).limit(1))
        if exact:
            return exact

        # contains search
        contains = list(db["foods"].find(
            {"name": {"$regex": re.escape(query), "$options": "i"}},
            {"_id": 0, "name": 1, "price": 1, "category": 1, "description": 1}
        ).limit(limit * 3))

        # fallback to all names if nothing contains
        pool = contains or list(db["foods"].find({}, {"_id": 0, "name": 1, "price": 1, "category": 1, "description": 1}))

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
    """Single-line, policy-compliant item detail."""
    name = item.get("name", "Item")
    price = item.get("price")
    cat = (item.get("category") or "").rstrip("s")
    desc = (item.get("description") or "").strip()
    # price format
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

# --- Pretty recent-order formatting helpers ---
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
        qty = int(it.get("qty") or it.get("quantity") or 1)
        total_qty += qty
        if len(parts) < max_items and name:
            parts.append(f"{name} ×{qty}")
    more = max(0, len(items) - len(parts))
    preview = ", ".join(parts) + (f", +{more} more" if more > 0 else "")
    return total_qty, preview

def get_user_recent_orders_detailed(user_id: Optional[str], limit=MAX_RECENT):
    """
    Fetch recent orders with normalized datetime `dt` chosen from:
    date | order_date | created_at. Sorted by dt desc.
    Returns: [{order_id, dt, items:[{name,qty},...], amount, status}, ...]
    """
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
            {"$addFields": {"qty": {"$ifNull": ["$items.qty", {"$ifNull": ["$items.quantity", 1]}]}}},
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
                            ]}}},
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
            qty = int(it.get("qty") or it.get("quantity") or 1)
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
            temperature=0.2,  # tighter to avoid irrelevant outputs
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
    """
    Extra guardrails so the LLM never goes off-menu.
    We feed it the exact menu list and strict rules.
    """
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

# ---------------- Payment reason intent (NEW) ----------------
_PAYMENT_REASON_KEYWORDS = (
    "why my payment", "why was my payment", "payment failed", "unsuccessful payment",
    "payment unsuccessful", "payment declined", "why did my payment fail",
    "why payment failed", "payment issue", "payment problem", "card declined",
    "stripe decline", "why was it declined", "why was it cancelled", "why it failed",
    "why did it fail", "why didn't it go through", "why it didn’t go through",
    "why was my order payment", "explain my payment", "transaction failed", "why my transaction failed",
    "why did my transaction fail", "why was my transaction declined"
)
def is_payment_reason_query(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in _PAYMENT_REASON_KEYWORDS)

def _latest_order_for_user(user_id: Optional[str]):
    """Return the most recent order for this user (sorted by `date` desc)."""
    if db is None or not user_id or user_id == "guest":
        return None
    try:
        flt = _possible_user_id_filters(user_id)
        if not flt:
            return None
        doc = db["orders"].find_one(
            flt,
            sort=[("date", -1), ("_id", -1)]
        )
        return doc
    except Exception:
        log.exception("_latest_order_for_user failed")
        return None

def _map_stripe_reason(code: str, decline_code: str = "", fallback: str = "") -> str:
    """Human-friendly reason from stored Stripe codes."""
    code_n = _n(code)
    decline_n = _n(decline_code)
    if decline_n and decline_n in STRIPE_ERRORS["decline_codes"]:
        return STRIPE_ERRORS["decline_codes"][decline_n]
    if code_n and code_n in STRIPE_ERRORS["codes"]:
        return STRIPE_ERRORS["codes"][code_n]
    return fallback or "The payment didn’t complete. Please try again, use a different card, or contact your bank."

def _explain_payment_outcome(order: dict, user_asked_failed: bool) -> str:
    """Concise explanation using order.paymentInfo; corrects user if they think it failed but it succeeded."""
    if not order:
        return "I couldn’t find any payments on your account yet."

    when = _fmt_date(order.get("date") or order.get("dt") or order.get("created_at"))
    amt = order.get("amount")
    try:
        amt_str = f"${int(float(amt))}" if float(amt).is_integer() else f"${float(amt):.2f}"
    except Exception:
        amt_str = f"${amt}" if amt is not None else ""

    pinfo = order.get("paymentInfo") or {}
    status = (pinfo.get("status") or "").strip().lower()  # 'succeeded' | 'failed'
    success_msg = (pinfo.get("successMessage") or "").strip()
    err_code = (pinfo.get("errorCode") or "").strip()
    err_msg = (pinfo.get("errorMessage") or "").strip()

    # Try to recover decline_code from errorMessage (optional)
    decline_code = ""
    m = re.search(r"decline[_\s-]?code\s*[:=]\s*([a-zA-Z0-9_ -]+)", err_msg, re.I)
    if m:
        decline_code = _n(m.group(1))

    if status == "succeeded":
        # If the user asked "why did it fail" but it actually succeeded, be explicit.
        base = f"Your most recent payment on {when} for {amt_str} was successful."
        if user_asked_failed:
            return base  # short and clear
        return f"{base} {success_msg}".strip()

    if status == "failed":
        reason = _map_stripe_reason(err_code, decline_code=decline_code, fallback=err_msg)
        if err_code:
            return f"Your most recent payment on {when} for {amt_str} was unsuccessful. Reason: {err_code} — {reason}"
        return f"Your most recent payment on {when} for {amt_str} was unsuccessful. {reason}"

    return f"I found an order on {when} for {amt_str}, but I couldn’t confirm the payment status yet."

# ---------------- API Models ----------------
class ChatReq(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MSG_LEN)
    userId: Optional[str] = Field(default=None, max_length=120)

class ChatResp(BaseModel):
    reply: str

# ---------------- App ----------------
app = FastAPI(title="Tomato Chatbot API", version="1.7.0")

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
        "version": "1.7.0",
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
    req_id = getattr(request.state, "req_id", "n/a")

    if response is not None:
        response.headers["X-Echo-UserId"] = str(user_id or "")

    # --- 1) Stripe raw error decoder ---
    if is_stripe_query(user_msg):
        reply = explain_stripe_error(user_msg)
        if response is not None:
            response.headers["X-Answer-Source"] = "rule:stripe"
        return ChatResp(reply=reply)

    # --- 2) "Why did my payment/transaction fail?" (requires login) ---
    if is_payment_reason_query(user_msg):
        if not user_id or user_id == "guest":
            text = "Please log in so I can check your recent payments. Once you’re signed in, ask again."
            if response is not None:
                response.headers["X-Answer-Source"] = "rule:pay_reason_login_required"
            return ChatResp(reply=text)

        last = _latest_order_for_user(user_id)
        if not last:
            text = "I couldn’t find any payments on your account yet."
            if response is not None:
                response.headers["X-Answer-Source"] = "rule:pay_reason_none"
            return ChatResp(reply=text)

        asked_failed = any(w in user_msg.lower() for w in ("fail", "failed", "declin", "unsuccessful", "didn't go through", "didn’t go through", "cancel"))
        text = _explain_payment_outcome(last, user_asked_failed=asked_failed)
        if response is not None:
            response.headers["X-Answer-Source"] = "rule:pay_reason_from_db"
            response.headers["X-Order-Id"] = str(last.get("_id"))
        return ChatResp(reply=text)

    # --- 3) Previous orders (detailed) ---
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

    # --- 4) Popularity questions (guarded LLM) ---
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

    # --- 5) Item detail (by name) ---
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

    # --- 6) Category-first answers ---
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

    # --- 7) LLM fallback (still guarded by menu context) ---
    db_context = build_context(user_msg, user_id)
    draft = "How can I help with our menu, your order, or delivery?"
    content = f"{db_context}\nCustomer: {user_msg}\nDraft: {draft}\nFollow the rules above."
    answer = guarded_rewrite(user_msg, draft) if FORCE_LLM else llm_compose(SYSTEM_PROMPT, content)
    if response is not None:
        response.headers["X-Answer-Source"] = "llm" if FORCE_LLM else "llm:plain"

    # Persist memory (best-effort)
    if memory and user_id:
        try:
            memory.add(user_msg, user_id=user_id, metadata={"app_id": "tomato", "role": "user"})
            memory.add(answer,   user_id=user_id, metadata={"app_id": "tomato", "role": "assistant"})
        except Exception:
            pass

    return ChatResp(reply=answer if isinstance(answer, str) else "")