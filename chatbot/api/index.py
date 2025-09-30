import os
from flask import Flask, request, jsonify
import main  # your existing file in the project root (chatbot/main.py)

app = Flask(__name__)

# Optional: preseed menu on cold start
try:
    main.bootstrap_foods_if_empty()
except Exception:
    pass

@app.get("/health")
def health():
    db_ok = True
    try:
        main.mongo.admin.command("ping")
    except Exception:
        db_ok = False
    return jsonify({
        "ok": True,
        "db": main.DB_NAME,
        "db_ok": db_ok,
        "model": main.OPENAI_MODEL,
        "memory_enabled": bool(main.memory),
    })

@app.post("/chat")
def chat():
    # auth like your FastAPI route
    if request.headers.get("x-service-auth", "") != (main.SHARED_SECRET or ""):
        return jsonify({"detail": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    user_msg = (body.get("message") or "").strip()
    if not user_msg:
        return jsonify({"detail": "message is required"}), 400
    user_id = body.get("userId") or None

    # Popularity quick path
    if main.is_popularity_query(user_msg):
        cat = main.category_from_query(user_msg)
        names = main.top_items_from_orders(limit=3, category=cat) or main.top_items_from_foods(limit=3, category=cat)
        if names:
            if cat:
                return jsonify({"reply": f"Our most-ordered {cat.rstrip('s')} right now: {', '.join(names)}."})
            return jsonify({"reply": "Top items customers are ordering: " + ", ".join(names) + "."})

    # Category quick path
    cat = main.category_from_query(user_msg)
    if cat:
        fetch_map = {
            "sandwich": main.get_sandwich_names,
            "rolls":    main.get_rolls_names,
            "salad":    main.get_salad_names,
            "desserts": main.get_desserts_names,
            "cake":     main.get_cake_names,
            "pasta":    main.get_pasta_names,
            "noodles":  main.get_noodles_names,
            "veg":      main.get_veg_names,
        }
        fetcher = fetch_map.get(cat)
        if fetcher:
            names = fetcher(limit=20)
            if names:
                return jsonify({"reply": f"Our {cat.rstrip('s')} options include: {', '.join(names[:10])}."})

    # LLM fallback
    db_context = main.build_context(user_msg, user_id)
    prompt = f"{db_context}\nCustomer: {user_msg}\nSupport Agent:"

    try:
        completion = main.client.chat.completions.create(
            model=main.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": main.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=main.OPENAI_MAX_TOKENS,
            timeout=main.OPENAI_TIMEOUT,
        )
        answer = (completion.choices[0].message.content or "").strip()
    except (main.APIConnectionError, main.RateLimitError, main.APIStatusError):
        popular = main.get_popular_items()
        if popular:
            return jsonify({"reply": "I’m having trouble reaching the assistant. Popular dishes: " + ", ".join(popular[:10]) + "."})
        return jsonify({"detail": "Chat service upstream error"}), 502
    except Exception:
        popular = main.get_popular_items()
        if popular:
            return jsonify({"reply": "I’m having trouble reaching the assistant. Popular dishes: " + ", ".join(popular[:10]) + "."})
        return jsonify({"detail": "Chat service upstream error"}), 502

    # best-effort memory
    if main.memory and user_id:
        try:
            main.memory.add(user_msg, user_id=user_id, metadata={"app_id":"tomato","role":"user"})
            main.memory.add(answer,   user_id=user_id, metadata={"app_id":"tomato","role":"assistant"})
        except Exception:
            pass

    return jsonify({"reply": answer})