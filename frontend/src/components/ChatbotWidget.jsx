import { useContext, useEffect, useRef, useState } from "react";
import { StoreContext } from "../context/StoreContext";

const ChatbotWidget = () => {
  const { url } = useContext(StoreContext);

  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState(() => {
    const cached = localStorage.getItem("tomatoai:messages");
    return cached
      ? JSON.parse(cached)
      : [
          {
            role: "assistant",
            content:
              "Hi! I’m TomatoAI. Ask me about dishes, delivery status, or your previous orders.",
            ts: Date.now(),
          },
        ];
  });

  const controllerRef = useRef(null);
  const inputRef = useRef(null);
  const endRef = useRef(null);

  // Persist messages (no per-user storage)
  useEffect(() => {
    localStorage.setItem("tomatoai:messages", JSON.stringify(messages));
  }, [messages]);

  // Auto-scroll
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

    // Cancel any in-flight request
    controllerRef.current?.abort?.();
    const ac = new AbortController();
    controllerRef.current = ac;

    // Push user message
    setMessages((m) => [...m, { role: "user", content: text, ts: Date.now() }]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch(url + "/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // No userId here — backend will handle login-required messaging
        body: JSON.stringify({ message: text }),
        signal: ac.signal,
      });

      let replyText = "Sorry — something went wrong.";
      if (res.ok) {
        const data = await res.json();
        replyText =
          typeof data?.reply === "string" && data.reply.length > 0
            ? data.reply
            : "…";
      } else {
        try {
          const err = await res.json();
          replyText = err?.error
            ? `Sorry — ${err.error}.`
            : `Sorry — HTTP ${res.status}.`;
        } catch {
          replyText = `Sorry — HTTP ${res.status}.`;
        }
      }

      setMessages((m) => [
        ...m,
        { role: "assistant", content: replyText, ts: Date.now() },
      ]);
    } catch (err) {
      if (ac.signal.aborted) return;
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: `Sorry — ${String(err?.message || "request failed")}.`,
          ts: Date.now(),
        },
      ]);
    } finally {
      if (!ac.signal.aborted) setLoading(false);
    }
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
      {/* Toggle button */}
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

          {/* Messages (only text + time) */}
          <div
            className="flex-1 overflow-y-auto px-3 py-2 space-y-2"
            role="log"
            aria-live="polite"
            aria-relevant="additions"
          >
            {messages.map((m, i) => (
              <div
                key={i}
                className={`flex ${
                  m.role === "user" ? "justify-end" : "justify-start"
                }`}
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