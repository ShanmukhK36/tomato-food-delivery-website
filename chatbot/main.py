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
    # ... (unchanged static foods list)
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
    # ... (unchanged)
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
# ... (unchanged helpers)
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

# ... (menu helpers etc. unchanged)

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
            # your Node first, then fallbacks
            candidates=["/cart/add", "/cart/items", "/cart"],
            json=body,
            timeout=6.0,
        )

    def remove_from_cart(self, item_id: str, qty: int = 1, user_id: Optional[str] = None):
        body = {"itemId": item_id, "qty": max(1, int(qty))}
        if user_id:
            body["userId"] = user_id
        return self._post_try(
            candidates=["/cart/remove", "/cart/items/remove"],
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

    # ---- ORDER / CHECKOUT ----
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
        # Usually not needed for Stripe Checkout, but keep as fallback
        return self._post_try(
            candidates=["/order/confirm", "/order/complete", "/order/finalize"],
            json={"paymentIntentId": payload.payment_intent_id},
            timeout=8.0,
        )

    # ---- CART (clear) ----
    def clear_cart(self, user_id: Optional[str] = None):
        # Prefer explicit clear endpoints, then DELETE fallbacks, then POST fallbacks,
        # then fallback: fetch items and remove everything via /cart/remove.
        q = f"?userId={user_id}" if user_id else ""
        # 1) Try POST-like clears
        try:
            return self._post_try(
                candidates=[f"/cart/clear{q}", f"/cart/reset{q}", f"/cart/empty{q}"],
                json={"userId": user_id} if user_id else {},
                timeout=6.0,
            )
        except Exception:
            pass

        # 2) Try DELETE-style clears (no body)
        last_err = None
        for p in [f"/cart/items{q}", f"/cart{q}"]:
            try:
                r = self.s.delete(self._url(p), timeout=6.0)
                data = self._safe_json(r)
                if 200 <= r.status_code < 300:
                    return data
                last_err = f"{p} -> {r.status_code} {(r.text or '')[:200]}"
            except Exception as e:
                last_err = f"{p} -> {e}"

        # 3) Fallback: fetch cart, then remove everything via /cart/remove
        try:
            current = self.get_cart(user_id=user_id) or {}
            cart_map = (
                current.get("cartData")
                or current.get("data", {}).get("cartData")
                or {}
            )
            # If we received items array instead of a map, reduce to id->qty when possible
            if not cart_map:
                items = (
                    current.get("items")
                    or current.get("cart", {}).get("items")
                    or current.get("data", {}).get("items")
                    or current.get("result", {}).get("items")
                    or []
                )
                tmp = {}
                for it in items:
                    _id = it.get("_id") or it.get("itemId") or it.get("id")
                    _qty = it.get("qty") or it.get("quantity") or 1
                    if _id:
                        tmp[str(_id)] = int(_qty or 1)
                cart_map = tmp

            # Nothing to clear
            if not isinstance(cart_map, dict) or not cart_map:
                return {"success": True, "items": []}

            # Remove in as few calls as possible (qty-aware), otherwise 1-by-1
            for item_id, qty in list(cart_map.items()):
                qty = max(1, int(qty or 1))
                try:
                    self.remove_from_cart(item_id=item_id, qty=qty, user_id=user_id)
                except Exception:
                    for _ in range(qty):
                        try:
                            self.remove_from_cart(item_id=item_id, qty=1, user_id=user_id)
                        except Exception:
                            break

            # Re-check cart; if empty, declare success
            verify = self.get_cart(user_id=user_id) or {}
            v_map = (
                verify.get("cartData")
                or verify.get("data", {}).get("cartData")
                or {}
            )
            v_items = (
                verify.get("items")
                or verify.get("cart", {}).get("items")
                or verify.get("data", {}).get("items")
                or verify.get("result", {}).get("items")
                or []
            )
            emptied = (isinstance(v_map, dict) and len(v_map) == 0) or (isinstance(v_items, list) and len(v_items) == 0)
            return {"success": True, "items": []} if emptied else {"success": False, "items": v_items}
        except Exception as e:
            raise RuntimeError(f"clear_cart fallback failed: {last_err or e}")

# === ORDERING intent extraction (multi-item support) ===
# Make "show cart" strict so "add ... to cart" doesn't misfire as show_cart.
SHOW_CART_PATTERNS = (
    r"\b(show|view|see)\b.*\b(cart|basket|bag)\b",
    r"\bwhat'?s in (my )?cart\b",
    r"\bshowcart\b",
    r"\bviewcart\b",
    r"\bmy\s*cart\b",
    r"^(cart|basket|bag)$",
)
CLEAR_CART_PATTERNS = (
    r"\b(clear|empty|flush|clean|reset|remove\s+all)\b.*\b(cart|basket|bag)\b",
    r"\b(clear|empty)\s*(my\s*)?(cart|basket|bag)\b",
    r"\b(delete|remove)\s*(everything|all)\s*(from\s*)?(my\s*)?(cart|basket|bag)\b",
)
CHECKOUT_PATTERNS = (r"\b(check ?out|pay|proceed to payment|place (the )?order)\b",)
CONFIRM_PATTERNS = (r"\b(confirm|finalize)\b.*\b(payment|order)\b",)
ADD_PATTERNS = (r"\b(add|order|get|i'?ll have|i want)\b",)

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
    # ... (unchanged)
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

def extract_action(user_msg: str) -> Optional[dict]:
    t = (user_msg or "").strip()
    low = t.lower()

    # ✅ check clear first
    for p in CLEAR_CART_PATTERNS:
        if re.search(p, low, flags=re.IGNORECASE):
            return {"type": "clear_cart", "slots": {}}

    # then show cart (stricter patterns)
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

    return None

# ---------------- API Models ----------------
class ChatReq(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MSG_LEN)
    userId: Optional[str] = Field(default=None, max_length=120)

class ChatResp(BaseModel):
    reply: str

# ---------------- App ----------------
app = FastAPI(title="Tomato Chatbot API", version="1.9.1-ordering")

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
        "version": "1.9.1-ordering",
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

    # === ORDERING (cart, checkout, confirm) ===
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

                # Normalize different backend shapes
                payload = cart or {}
                items = (
                    payload.get("items")
                    or payload.get("cart", {}).get("items")
                    or payload.get("data", {}).get("items")
                    or payload.get("result", {}).get("items")
                    or []
                )

                # Handle Node template shape: { success, cartData: { "<foodId>": qty, ... } }
                if not items:
                    cart_map = (
                        payload.get("cartData")
                        or payload.get("data", {}).get("cartData")
                        or {}
                    )
                    if isinstance(cart_map, dict) and cart_map:
                        items = _items_from_cart_map(cart_map)

                if not items:
                    if response is not None:
                        response.headers["X-Answer-Source"] = "order:cart_empty"
                    return ChatResp(reply="Your cart is empty. Say “add Veg Noodles x 1”.")

                # Build a friendly preview
                parts = []
                for it in items[:5]:
                    nm = (
                        (it.get("name")
                         or it.get("itemId")
                         or it.get("title")
                         or it.get("product")
                         or "item").strip()
                    )
                    q = int(it.get("qty") or it.get("quantity") or 1)
                    parts.append(f"{nm} ×{q}")

                more = max(0, len(items) - len(parts))
                suffix = f" (+{more} more)" if more else ""

                if response is not None:
                    response.headers["X-Answer-Source"] = "order:show_cart"
                    response.headers["X-Cart-Should-Refresh"] = "1"

                return ChatResp(reply=f"In your cart: {', '.join(parts)}{suffix}. Say “checkout” to continue.")
            except Exception as e:
                log.error("get_cart failed for user_id=%s: %s", user_id, e)
                if REQUIRE_AUTH_FOR_ORDER and not (jwt_token or fwd_cookie):
                    return ChatResp(reply="I couldn’t load your cart. Please make sure you’re signed in and the app forwards your login to chat.")
                return ChatResp(reply="I couldn’t load your cart. Please try again.")

        if t == "clear_cart":
            try:
                res = oc.clear_cart(user_id=user_id)

                # Normalize shapes to detect success/emptiness
                payload = res or {}
                success_flag = bool(payload.get("success") in (True, "true", 1))
                items = (
                    payload.get("items")
                    or payload.get("cart", {}).get("items")
                    or payload.get("data", {}).get("items")
                    or payload.get("result", {}).get("items")
                    or []
                )
                cart_map = (
                    payload.get("cartData")
                    or payload.get("data", {}).get("cartData")
                    or {}
                )
                cleared = success_flag or (isinstance(items, list) and len(items) == 0) or (isinstance(cart_map, dict) and len(cart_map) == 0)

                if response is not None:
                    response.headers["X-Answer-Source"] = "order:clear_cart"
                    response.headers["X-Cart-Should-Refresh"] = "1"

                if cleared:
                    return ChatResp(reply="Your cart is now empty.")
                return ChatResp(reply="I tried to clear your cart. If anything remains, say “show cart” to refresh.")
            except Exception as e:
                log.error("clear_cart failed for user_id=%s: %s", user_id, e)
                if REQUIRE_AUTH_FOR_ORDER and not (jwt_token or fwd_cookie):
                    return ChatResp(reply="Please log in to clear your cart, then try again.")
                return ChatResp(reply="I couldn’t clear your cart right now. Please try again.")

        if t == "checkout":
            addr, contact = extract_address_and_contact_from_mem(user_id)
            method = slots.get("payment_method", "card")
            try:
                res = oc.checkout(CheckoutPayload(address=addr, contact=contact, method=method))
                checkout_url = (res.get("session_url") or res.get("checkoutUrl") or res.get("url") or "")
                client_secret = res.get("clientSecret") or ""
                if response is not None:
                    response.headers["X-Answer-Source"] = "order:checkout"
                    if checkout_url:
                        response.headers["X-Checkout-Url"] = checkout_url
                    if client_secret:
                        response.headers["X-Client-Secret"] = client_secret
                if checkout_url:
                    return ChatResp(reply="Secure payment link is ready. Complete payment to place your order.")
                if client_secret:
                    return ChatResp(reply="Payment is ready. I’ll help you finalize it in the app.")
                return ChatResp(reply="Checkout is prepared. Follow the payment steps to finish.")
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