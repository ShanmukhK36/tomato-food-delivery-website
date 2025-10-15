import express from "express";
import { randomUUID } from "crypto";
import jwt from "jsonwebtoken";

const router = express.Router();

const PY_CHAT_URL = process.env.PY_CHAT_URL ?? "http://localhost:8000/chat";
const SHARED_SECRET = process.env.SHARED_SECRET ?? "dev-secret";
const JWT_KEY = process.env.JWT_KEY; // must match what you use to sign user JWTs
const UPSTREAM_TIMEOUT_MS = Number(process.env.UPSTREAM_TIMEOUT_MS ?? 8000);
const MAX_MESSAGE_LEN = Number(process.env.MAX_MESSAGE_LEN ?? 2000);
const MAX_RETRIES = Number(process.env.UPSTREAM_RETRIES ?? 2);

// ---------- CORS for this route (keeps things explicit) ----------
const FRONTEND_URL =
  process.env.FRONTEND_URL ||
  "https://tomato-food-delivery-website-umber.vercel.app";

router.use((req, res, next) => {
  const origin = req.headers.origin;
  if (origin && origin === FRONTEND_URL) {
    res.setHeader("Access-Control-Allow-Origin", origin);
  }
  res.setHeader("Vary", "Origin");
  res.setHeader("Access-Control-Allow-Credentials", "true");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  res.setHeader(
    "Access-Control-Allow-Headers",
    [
      "Content-Type",
      "Authorization",
      "X-Requested-With",
      "X-User-JWT",
      "X-User-Cookie",
      "x-service-auth",
      "x-request-id",
    ].join(", ")
  );
  // Let the browser read debug/checkout headers
  res.setHeader(
    "Access-Control-Expose-Headers",
    [
      "X-Answer-Source",
      "X-Request-Id",
      "X-Cart-Should-Refresh",
      "X-Checkout-Url",
      "X-Client-Secret",
      "X-Order-Id",
      "X-Echo-UserId",
    ].join(", ")
  );

  if (req.method === "OPTIONS") return res.status(204).end();
  next();
});

// ---------- helpers ----------
function validateBody(body) {
  if (!body || typeof body.message !== "string") return "message must be a string";
  const msg = body.message.trim();
  if (!msg) return "message is required";
  if (msg.length > MAX_MESSAGE_LEN) return `message too long (>${MAX_MESSAGE_LEN})`;
  return null;
}
function logJSON(level, obj) {
  (console[level] || console.log)(JSON.stringify(obj));
}
async function fetchWithTimeout(url, init, timeoutMs) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(new Error("upstream timeout")), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: ac.signal });
  } finally {
    clearTimeout(t);
  }
}
function shouldRefreshFromAnswerSource(src) {
  if (!src) return false;
  // any cart-affecting flows should refresh
  return [
    "order:add_multi",
    "order:clear_cart",
    "order:show_cart",
    "order:cart_empty",
  ].includes(src);
}
// ----- JWT / userId derivation -----
function getBearerToken(req) {
  const auth = req.headers.authorization || req.headers.Authorization;
  if (auth && typeof auth === "string" && auth.startsWith("Bearer ")) {
    return auth.slice(7).trim();
  }
  return null;
}
function getCookieToken(req) {
  const raw = req.headers.cookie;
  if (!raw) return null;
  const pair = raw.split(";").map(s => s.trim()).find(p => p.startsWith("token="));
  if (!pair) return null;
  return decodeURIComponent(pair.split("=").slice(1).join("="));
}
function getUserIdFromRequest(req) {
  try {
    const token = getBearerToken(req) || getCookieToken(req);
    if (token && JWT_KEY) {
      const payload = jwt.verify(token, JWT_KEY);
      if (payload?.id) return String(payload.id);
    }
  } catch { /* ignore */ }
  const debugHeader = req.headers["x-debug-userid"];
  const debugQuery = req.query?.debug_user_id;
  const override =
    (typeof debugHeader === "string" && debugHeader.trim()) ||
    (typeof debugQuery === "string" && debugQuery.trim());
  return override || null;
}

// ---------------- /api/chat ----------------
router.post("/", express.json({ limit: "10kb" }), async (req, res) => {
  const reqId = req.headers["x-request-id"] || randomUUID();

  const errMsg = validateBody(req.body);
  if (errMsg) return res.status(400).json({ error: errMsg, requestId: reqId });

  const message = String(req.body.message).trim();
  const userId = getUserIdFromRequest(req) || undefined;
  const upstreamBody = userId ? { message, userId } : { message };

  // Forward auth headers so FastAPI can call /cart & /order
  const xUserJwt = req.get("X-User-JWT") || "";
  const xUserCookie = req.get("X-User-Cookie") || req.headers.cookie || "";
  const authHeader = req.get("Authorization") || "";

  const buildInit = () => ({
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-service-auth": SHARED_SECRET, // must equal FastAPI SHARED_SECRET
      "x-request-id": reqId,
      "X-User-JWT": xUserJwt,
      "X-User-Cookie": xUserCookie,
      "Authorization": authHeader,
    },
    body: JSON.stringify(upstreamBody),
  });

  const tryOnce = () => fetchWithTimeout(PY_CHAT_URL, buildInit(), UPSTREAM_TIMEOUT_MS);

  let upstreamRes;
  let attempt = 0;
  let lastErr;
  while (attempt < MAX_RETRIES) {
    attempt++;
    try {
      upstreamRes = await tryOnce();
      if ([502, 503, 504].includes(upstreamRes.status) && attempt < MAX_RETRIES) {
        await new Promise(r => setTimeout(r, 250 * attempt));
        continue;
      }
      break;
    } catch (e) {
      lastErr = e;
      if (attempt >= MAX_RETRIES) break;
      await new Promise(r => setTimeout(r, 250 * attempt));
    }
  }

  if (!upstreamRes) {
    logJSON("error", { reqId, route: "/api/chat", msg: "no upstream response", error: String(lastErr) });
    return res.status(502).json({ error: "Chat service unavailable", requestId: reqId });
  }

  const upstreamReqId = upstreamRes.headers.get("x-request-id") || reqId;
  const upstreamAnswerSource = upstreamRes.headers.get("x-answer-source") || undefined;
  const upstreamEchoUserId = upstreamRes.headers.get("x-echo-userid") || "";

  // Bubble back useful headers (incl. cart refresh)
  const expose = {
    "X-Answer-Source": upstreamAnswerSource,
    "X-Request-Id": upstreamReqId,
    "X-Echo-UserId": upstreamEchoUserId,
    "X-Cart-Should-Refresh": upstreamRes.headers.get("x-cart-should-refresh"),
    "X-Checkout-Url": upstreamRes.headers.get("x-checkout-url"),
    "X-Client-Secret": upstreamRes.headers.get("x-client-secret"),
    "X-Order-Id": upstreamRes.headers.get("x-order-id"),
  };
  // If upstream forgot to flag refresh but the flow requires it, set it here.
  if (!expose["X-Cart-Should-Refresh"] && shouldRefreshFromAnswerSource(upstreamAnswerSource)) {
    expose["X-Cart-Should-Refresh"] = "1";
  }
  Object.entries(expose).forEach(([k, v]) => v && res.setHeader(k, v));

  const ct = (upstreamRes.headers.get("content-type") || "").toLowerCase();
  let data;
  try {
    if (ct.includes("application/json")) {
      data = await upstreamRes.json();
    } else {
      const raw = await upstreamRes.text();
      data = {
        error: "upstream_not_json",
        status: upstreamRes.status,
        contentType: ct || "unknown",
        raw: raw.slice(0, 1000),
      };
    }
  } catch {
    data = { error: "upstream_parse_error", status: upstreamRes.status };
  }

  // Map 5xx to 502 so the browser knows it's a gateway issue
  const passthroughStatus = upstreamRes.status >= 500 ? 502 : upstreamRes.status;

  // Ensure we always send a JSON object and include IDs
  if (typeof data !== "object" || data === null) data = {};
  if (upstreamAnswerSource && data.answerSource == null) data.answerSource = upstreamAnswerSource;
  data.requestId = upstreamReqId;
  data.echoUserId = upstreamEchoUserId || null;
  data.attachedUserId = userId || null;

  // If success but no reply string, normalize to empty reply
  if (passthroughStatus < 400 && (typeof data.reply !== "string")) {
    data.reply = typeof data.raw === "string"
      ? `Sorry — upstream error: ${data.raw.slice(0, 240)}`
      : (data.reply ?? "");
  }

  res.setHeader("Cache-Control", "no-store");

  logJSON("info", {
    reqId,
    route: "POST /api/chat",
    upstreamUrl: PY_CHAT_URL,
    contentType: ct || "unknown",
    upstreamStatus: upstreamRes.status,
    mappedStatus: passthroughStatus,
    len: message.length,
    attempts: attempt,
    answerSource: upstreamAnswerSource ?? null,
    hasUserId: Boolean(userId),
    echoUserId: upstreamEchoUserId || null,
    fwdJwt: Boolean(xUserJwt),
    fwdCookie: Boolean(xUserCookie),
  });

  return res.status(passthroughStatus).json(data);
});

export default router;