import { useContext, useEffect, useRef, useState } from "react";
import { StoreContext } from "../context/StoreContext";

const uid = () => Math.random().toString(36).slice(2, 10);
const getUserId = () => localStorage.getItem("userId") || "guest";
const getToken = () => localStorage.getItem("token") || "";
const keyFor = (userId) => `tomatoai:${userId}:messages`;

const ChatbotWidget = () => {
  const { url } = useContext(StoreContext);
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const [userId, setUserId] = useState(getUserId());

  const [messages, setMessages] = useState(() => {
    const u = getUserId();
    const cached = localStorage.getItem(keyFor(u));
    return cached
      ? JSON.parse(cached)
      : [
          {
            id: uid(),
            role: "assistant",
            content:
              "Hi! I’m TomatoAI. Ask me about dishes, your cart, checkout, or delivery.",
            ts: Date.now(),
          },
        ];
  });

  const controllerRef = useRef(null);
  const inputRef = useRef(null);
  const endRef = useRef(null);

  // Keep userId synced with localStorage (login/logout in another tab)
  useEffect(() => {
    const sync = () => {
      const current = getUserId();
      setUserId((prev) => (prev !== current ? current : prev));
    };
    const onStorage = (e) => {
      if (!e || e.key === "userId" || e.key === "token") sync();
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", sync);
    const timer = setInterval(sync, 1000);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", sync);
      clearInterval(timer);
    };
  }, []);

  // When userId changes, load that user's cached thread
  useEffect(() => {
    const cached = localStorage.getItem(keyFor(userId));
    setMessages(
      cached
        ? JSON.parse(cached)
        : [
            {
              id: uid(),
              role: "assistant",
              content:
                "Hi! I’m TomatoAI. Ask me about dishes, your cart, checkout, or delivery.",
              ts: Date.now(),
            },
          ]
    );
  }, [userId]);

  // Persist messages per-user
  useEffect(() => {
    localStorage.setItem(keyFor(userId), JSON.stringify(messages));
  }, [messages, userId]);

  // Autoscroll
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, open]);

  // Focus input on open
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  const sendMessage = async (e) => {
    e?.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    const currentUserId = getUserId();
    const token = getToken();
    if (currentUserId !== userId) setUserId(currentUserId);

    // Cancel any in-flight request
    controllerRef.current?.abort?.();
    const ac = new AbortController();
    controllerRef.current = ac;

    const userMsg = { id: uid(), role: "user", content: text, ts: Date.now() };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setLoading(true);

    const tryFetch = async (attempt = 1) => {
      try {
        if (!url) {
          throw new Error("API base URL missing from StoreContext.url");
        }

        // Build headers: send JWT if you have it; cookies go via credentials: 'include'
        const headers = { "Content-Type": "application/json" };
        if (token) {
          headers.Authorization = `Bearer ${token}`;
          headers["X-User-JWT"] = token; // lets Node forward explicitly as well
        }

        // Client-side debug (one-line, remove if noisy)
        console.log(
          "[ChatbotWidget] POST",
          `${url}/api/chat`,
          { hasJWT: Boolean(token), userId: currentUserId }
        );

        const res = await fetch(`${url}/api/chat`, {
          method: "POST",
          credentials: "include", // 🔴 ensures browser sends your login cookies
          headers,
          // Do NOT send userId in body — backend derives from JWT/cookie
          body: JSON.stringify({ message: text }),
          signal: ac.signal,
        });

        let data = {};
        try {
          data = await res.json();
        } catch {
          data = {};
        }

        if (!res.ok) {
          const rid =
            data?.requestId ||
            res.headers.get("x-request-id") ||
            "unknown";
          const msg = data?.detail || data?.error || `HTTP ${res.status}`;
          throw new Error(`${msg} · id=${rid}`);
        }

        const reply =
          typeof data.reply === "string" ? data.reply : "…";

        const botMsg = {
          id: uid(),
          role: "assistant",
          content: reply,
          ts: Date.now(),
        };
        setMessages((m) => [...m, botMsg]);
      } catch (err) {
        if (ac.signal.aborted) return;
        if (attempt < 2) {
          await new Promise((r) => setTimeout(r, 400 * attempt));
          return tryFetch(attempt + 1);
        }
        console.error(err);
        setMessages((m) => [
          ...m,
          {
            id: uid(),
            role: "assistant",
            content: `Sorry — ${String(err?.message || "request failed")}.`,
            ts: Date.now(),
            error: true,
            retryPayload: { text },
          },
        ]);
      } finally {
        if (!ac.signal.aborted) setLoading(false);
      }
    };

    tryFetch();
  };

  const retry = (payload) => {
    if (!payload?.text) return;
    setInput(payload.text);
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
    if (e.key === "Escape") setOpen(false);
  };

  return (
    <>
      {/* Floating button */}
      <button
        aria-label={open ? "Close TomatoAI chat" : "Open TomatoAI chat"}
        onClick={() => setOpen((v) => !v)}
        className="fixed bottom-5 right-5 z-50 rounded-full shadow-lg px-4 py-3 text-white bg-orange-500 hover:bg-orange-600 transition"
      >
        {open ? "✕" : "Chat"}
      </button>

      {/* Chat panel */}
      {open && (
        <div
          className="fixed bottom-20 right-5 z-50 w-80 sm:w-96 h-[560px] bg-white border border-gray-200 rounded-2xl shadow-2xl flex flex-col"
          role="dialog"
          aria-modal="true"
          aria-labelledby="tomatoai-title"
        >
          {/* Header */}
          <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
            <div id="tomatoai-title" className="font-semibold">
              TomatoAI
            </div>
            <button
              onClick={() => setOpen(false)}
              className="text-gray-500 hover:text-gray-700"
              aria-label="Close chat"
            >
              ✕
            </button>
          </div>

          {/* Messages */}
          <div
            className="flex-1 overflow-y-auto px-3 py-2 space-y-2"
            role="log"
            aria-live="polite"
            aria-relevant="additions"
          >
            {messages.map((m) => (
              <div
                key={m.id}
                className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
                    m.role === "user"
                      ? "bg-orange-500 text-white"
                      : "bg-gray-100 text-gray-900"
                  }`}
                >
                  <p className="whitespace-pre-wrap break-words">{m.content}</p>
                  <span className="mt-1 block text-[10px] opacity-70">
                    {new Date(m.ts).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                    {m.error && (
                      <>
                        {" · "}
                        <button
                          onClick={() => retry(m.retryPayload)}
                          className="underline underline-offset-2"
                          aria-label="Retry sending last message"
                        >
                          Retry
                        </button>
                      </>
                    )}
                  </span>
                </div>
              </div>
            ))}
            {loading && (
              <div className="text-xs text-gray-500 italic px-2">
                TomatoAI is typing…
              </div>
            )}
            <div ref={endRef} />
          </div>

          {/* Input */}
          <form onSubmit={sendMessage} className="p-3 border-t border-gray-200 flex gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              rows={1}
              placeholder="Ask about dishes, cart, or checkout…"
              className="flex-1 border border-gray-300 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-red-400 resize-none"
              aria-label="Type your message to TomatoAI"
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="rounded-xl bg-orange-500 hover:bg-orange-600 text-white px-3 py-2 text-sm disabled:opacity-60"
            >
              Send
            </button>
          </form>
        </div>
      )}
    </>
  );
};

export default ChatbotWidget;