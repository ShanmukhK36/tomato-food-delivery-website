import express from "express";
import { randomUUID } from "crypto";
import jwt from "jsonwebtoken";

const router = express.Router();

const PY_CHAT_URL = process.env.PY_CHAT_URL ?? "http://localhost:8000/chat";
const SHARED_SECRET = process.env.SHARED_SECRET ?? "dev-secret";
const JWT_KEY = process.env.JWT_KEY; // must match your user controller
const UPSTREAM_TIMEOUT_MS = Number(process.env.UPSTREAM_TIMEOUT_MS ?? 8000);
const MAX_MESSAGE_LEN = Number(process.env.MAX_MESSAGE_LEN ?? 2000);
const MAX_RETRIES = Number(process.env.UPSTREAM_RETRIES ?? 2); // total attempts

// ---------- CORS (allow your web app origin) ----------
const FRONTEND_URL =
  process.env.FRONTEND_URL ||
  "https://tomato-food-delivery-website-umber.vercel.app";

router.use((req, res, next) => {
  const origin = req.headers.origin;
  if (origin && origin === FRONTEND_URL) {
    res.setHeader("Access-Control-Allow-Origin", origin);
  }
  // vary for caches/CDN
  res.setHeader("Vary", "Origin");

  // allow auth and AJAX
  res.setHeader("Access-Control-Allow-Credentials", "true");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  res.setHeader(
    "Access-Control-Allow-Headers",
    "Content-Type, Authorization, X-Requested-With"
  );

  // quick end for preflight
  if (req.method === "OPTIONS") return res.status(204).end();
  next();
});

// ---------- helpers ----------
function validateBody(body) {
  if (!body || typeof body.message !== "string") return "message must be a string";
  const msg = body.message.trim();
  if (!msg) return "message is required";
  if (msg.length > MAX_MESSAGE_LEN) return `message too long (>${MAX_MESSAGE_LEN})`;
  // Ignore any client-sent userId; we derive it from JWT
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
  const parts = raw.split(";").map((s) => s.trim());
  const pair = parts.find((p) => p.startsWith("token="));
  if (!pair) return null;
  return decodeURIComponent(pair.split("=").slice(1).join("="));
}

/**
 * Return userId from a verified JWT ({ id: <mongoId> }) or null.
 * If no valid JWT, allow a DEV-ONLY override via header/query.
 */
function getUserIdFromRequest(req) {
  // 1) Real auth path: Authorization or cookie
  try {
    const token = getBearerToken(req) || getCookieToken(req);
    if (token && JWT_KEY) {
      const payload = jwt.verify(token, JWT_KEY);
      if (payload?.id) return String(payload.id);
    }
  } catch {
    // invalid/expired token → fall through to debug overrides
  }

  // 2) Dev override (only when no valid JWT): header or query param
  const debugHeader = req.headers["x-debug-userid"];
  const debugQuery = req.query?.debug_user_id;
  const override =
    (typeof debugHeader === "string" && debugHeader.trim()) ||
    (typeof debugQuery === "string" && debugQuery.trim());
  return override || null;
}

// ---------------- Route ----------------
router.post("/", express.json({ limit: "10kb" }), async (req, res) => {
  const reqId = req.headers["x-request-id"] || randomUUID();

  // Validate input (message only)
  const errMsg = validateBody(req.body);
  if (errMsg) {
    return res.status(400).json({ error: errMsg, requestId: reqId });
  }

  const message = String(req.body.message).trim();

  // Derive userId from JWT (or debug override). Do NOT trust client body.
  const userId = getUserIdFromRequest(req) || undefined;

  // Build upstream request (only include userId if present)
  const upstreamBody = userId ? { message, userId } : { message };

  const buildInit = () => ({
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-service-auth": SHARED_SECRET, // must match Python SHARED_SECRET
      "x-request-id": reqId, // pass through for tracing
    },
    body: JSON.stringify(upstreamBody),
  });

  const tryOnce = async () =>
    fetchWithTimeout(PY_CHAT_URL, buildInit(), UPSTREAM_TIMEOUT_MS);

  let upstreamRes;
  let attempt = 0;
  let lastErr;

  while (attempt < MAX_RETRIES) {
    attempt++;
    try {
      upstreamRes = await tryOnce();
      // Retry only on upstream server errors
      if ([502, 503, 504].includes(upstreamRes.status) && attempt < MAX_RETRIES) {
        await new Promise((r) => setTimeout(r, 250 * attempt));
        continue;
      }
      break;
    } catch (e) {
      lastErr = e;
      if (attempt >= MAX_RETRIES) break;
      await new Promise((r) => setTimeout(r, 250 * attempt));
    }
  }

  if (!upstreamRes) {
    logJSON("error", {
      reqId,
      route: "/api/chat",
      msg: "no upstream response",
      error: String(lastErr),
    });
    return res.status(502).json({ error: "Chat service unavailable", requestId: reqId });
  }

  // Map 5xx to 502 so we don't leak internal upstream codes
  const passthroughStatus = upstreamRes.status >= 500 ? 502 : upstreamRes.status;

  // Capture upstream headers
  const upstreamAnswerSource = upstreamRes.headers.get("x-answer-source") || undefined;
  const upstreamReqId = upstreamRes.headers.get("x-request-id") || reqId;
  const upstreamEchoUserId = upstreamRes.headers.get("x-echo-userid") || "";

  // Parse JSON safely
  let data;
  const isJson = upstreamRes.headers.get("content-type")?.includes("application/json");
  try {
    data = isJson ? await upstreamRes.json() : { error: "Invalid content-type from chatbot service" };
  } catch {
    data = { error: "Invalid JSON response from chatbot service" };
  }

  // Normalize successful-but-empty replies
  if (passthroughStatus < 400 && (typeof data?.reply !== "string" || data.reply.length === 0)) {
    data = { reply: "" };
  }

  // Surface answer source + echo userId for debugging
  if (data && typeof data === "object") {
    if (upstreamAnswerSource && data.answerSource == null) {
      data.answerSource = upstreamAnswerSource;
    }
    data.echoUserId = upstreamEchoUserId || null; // what FastAPI received
    data.attachedUserId = userId || null;        // what we attached
  }

  // Pass through helpful headers
  if (upstreamAnswerSource) res.setHeader("X-Answer-Source", upstreamAnswerSource);
  if (upstreamEchoUserId)   res.setHeader("X-Echo-UserId", upstreamEchoUserId);

  // Echo tracing id back
  res.setHeader("X-Request-Id", upstreamReqId);
  res.setHeader("Cache-Control", "no-store");

  logJSON("info", {
    reqId,
    route: "POST /api/chat",
    upstreamStatus: upstreamRes.status,
    mappedStatus: passthroughStatus,
    len: message.length,
    attempts: attempt,
    answerSource: upstreamAnswerSource ?? null,
    hasUserId: Boolean(userId),
    echoUserId: upstreamEchoUserId || null,
  });

  return res.status(passthroughStatus).json({ ...data, requestId: upstreamReqId });
});

export default router;