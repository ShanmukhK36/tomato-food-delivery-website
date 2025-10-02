import express from "express";
import { randomUUID } from "crypto";

const router = express.Router();

const PY_CHAT_URL = process.env.PY_CHAT_URL ?? "http://localhost:8000/chat";
const SHARED_SECRET = process.env.SHARED_SECRET ?? "dev-secret";
const UPSTREAM_TIMEOUT_MS = Number(process.env.UPSTREAM_TIMEOUT_MS ?? 8000);
const MAX_MESSAGE_LEN = Number(process.env.MAX_MESSAGE_LEN ?? 2000);
const MAX_RETRIES = Number(process.env.UPSTREAM_RETRIES ?? 2); // total attempts

// Simple schema (no deps)
function validateBody(body) {
  if (!body || typeof body.message !== "string") return "message must be a string";
  const msg = body.message.trim();
  if (!msg) return "message is required";
  if (msg.length > MAX_MESSAGE_LEN) return `message too long (>${MAX_MESSAGE_LEN})`;
  if (body.userId != null && typeof body.userId !== "string") return "userId must be a string";
  return null;
}

function logJSON(level, obj) {
  (console[level] || console.log)(JSON.stringify(obj));
}

async function fetchWithTimeout(url, init, timeoutMs) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: ac.signal });
  } finally {
    clearTimeout(t);
  }
}

router.post("/", express.json({ limit: "10kb" }), async (req, res) => {
  const reqId = req.headers["x-request-id"] || randomUUID();

  // Validate input
  const errMsg = validateBody(req.body);
  if (errMsg) {
    return res.status(400).json({ error: errMsg, requestId: reqId });
  }

  const { message, userId } = req.body;

  // Build upstream request once
  const buildInit = () => ({
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-service-auth": SHARED_SECRET, // must match Python SHARED_SECRET
      "x-request-id": reqId,           // pass through for tracing
    },
    body: JSON.stringify({ message, userId }),
  });

  const tryOnce = async () => {
    return fetchWithTimeout(PY_CHAT_URL, buildInit(), UPSTREAM_TIMEOUT_MS);
  };

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
    logJSON("error", { reqId, route: "/api/chat", msg: "no upstream response", error: String(lastErr) });
    return res.status(502).json({ error: "Chat service unavailable", requestId: reqId });
  }

  // Map 5xx to 502 so we don't leak internal upstream codes
  const passthroughStatus = upstreamRes.status >= 500 ? 502 : upstreamRes.status;

  // Capture upstream headers
  const upstreamAnswerSource = upstreamRes.headers.get("x-answer-source") || undefined;
  const upstreamReqId = upstreamRes.headers.get("x-request-id") || reqId;

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

  // Surface answer source in header and body (non-breaking)
  if (upstreamAnswerSource) {
    res.setHeader("X-Answer-Source", upstreamAnswerSource);
    if (data && typeof data === "object" && data.answerSource == null) {
      data.answerSource = upstreamAnswerSource;
    }
  }

  // Echo tracing id back
  res.setHeader("X-Request-Id", upstreamReqId);
  res.setHeader("Cache-Control", "no-store");

  logJSON("info", {
    reqId,
    route: "POST /api/chat",
    upstreamStatus: upstreamRes.status,
    mappedStatus: passthroughStatus,
    len: String(message).length,
    attempts: attempt,
    answerSource: upstreamAnswerSource ?? null,
  });

  return res.status(passthroughStatus).json({ ...data, requestId: upstreamReqId });
});

export default router;