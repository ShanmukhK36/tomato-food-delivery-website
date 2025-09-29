import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { StoreContext } from '../context/StoreContext';

const uid = () => Math.random().toString(36).slice(2, 10);

const ChatbotWidget = () => {
  const {url} = useContext(StoreContext);
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState(() => {
    const userId = localStorage.getItem("userId") || "guest";
    const cached = localStorage.getItem(`tomatoai:${userId}:messages`);
    return cached
      ? JSON.parse(cached)
      : [{ id: uid(), role: "assistant", content: "Hi! I’m TomatoAI. Ask me about restaurants, dishes, delivery status, or your orders.", ts: Date.now() }];
  });

  const userId = useMemo(() => localStorage.getItem("userId") || "guest", []);
  const controllerRef = useRef(null);
  const inputRef = useRef(null);
  const logRef = useRef(null);
  const endRef = useRef(null);

  // Persist messages per user
  useEffect(() => {
    localStorage.setItem(`tomatoai:${userId}:messages`, JSON.stringify(messages));
  }, [messages, userId]);

  // Scroll to bottom on open/new messages
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, open]);

  // Focus input when opening
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  const sendMessage = async (e) => {
    e?.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    // Abort any in-flight request
    controllerRef.current?.abort?.();
    const ac = new AbortController();
    controllerRef.current = ac;

    const userMsg = { id: uid(), role: "user", content: text, ts: Date.now() };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setLoading(true);

    const tryFetch = async (attempt = 1) => {
      try {
        const res = await fetch(url + "/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, userId }),
          signal: ac.signal
        });
        if (!res.ok) {
          let msg = `HTTP ${res.status}`;
          try {
            const err = await res.json();
            if (err?.error) msg = err.error;
            if (err?.requestId) msg += ` · id=${err.requestId}`;
           } catch {

           }
            throw new Error(msg);
        }
        const data = await res.json(); // { reply }
        const botMsg = {
          id: uid(),
          role: "assistant",
          content: typeof data.reply === "string" ? data.reply : "...",
          ts: Date.now()
        };
        setMessages((m) => [...m, botMsg]);
      } catch (err) {
        if (ac.signal.aborted) return; // user sent a new message / closed widget
        if (attempt < 2) {
          // simple backoff
          await new Promise((r) => setTimeout(r, 400 * attempt));
          return tryFetch(attempt + 1);
        }
        console.error(err);
        setMessages((m) => [
          ...m,
          {
            id: uid(),
            role: "assistant",
            content: `Sorry — ${String(err.message || 'request failed')}.`,
            ts: Date.now(),
            error: true,
            retryPayload: { text }
          }
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
    // Optionally auto-send:
    // setTimeout(() => sendMessage(), 0);
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
            <div id="tomatoai-title" className="font-semibold">TomatoAI</div>
            <button
              onClick={() => setOpen(false)}
              className="text-gray-500 hover:text-gray-700"
              aria-label="Close chat"
            >
              ✕
            </button>
          </div>

          {/* Messages (aria-live for SRs) */}
          <div
            ref={logRef}
            className="flex-1 overflow-y-auto px-3 py-2 space-y-2"
            role="log"
            aria-live="polite"
            aria-relevant="additions"
          >
            {messages.map((m) => (
              <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
                    m.role === "user" ? "bg-orange-500 text-white" : "bg-gray-100 text-gray-900"
                  }`}
                >
                  <p className="whitespace-pre-wrap break-words">{m.content}</p>
                  <span className="mt-1 block text-[10px] opacity-70">
                    {new Date(m.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
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
            {loading && <div className="text-xs text-gray-500 italic px-2">TomatoAI is typing…</div>}
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
              placeholder="Ask about dishes, orders…"
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