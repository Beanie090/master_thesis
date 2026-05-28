import os
import uuid

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify, render_template, request
import google.generativeai as genai

from companion import initialize_persona, run_companion
from helpers import get_custom_persona, get_history, get_user_info, init_db, save_message, save_persona
from prompts import BUILD_AGENT, LEAI_PERSONALITY, SETUP_AGENT

app = Flask(__name__)

_gemini_api_key = os.environ.get("GEMINI_API_KEY")
if not _gemini_api_key:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Add it to your .env file or environment "
        "before starting LEAi."
    )
genai.configure(api_key=_gemini_api_key)
init_db()

# Setup-phase history lives here — only populated during custom persona creation,
# before the persona has any stored conversation.
_setup_history: dict[str, list[dict]] = {}
SETUP_MAX_TURNS = 20


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start_session():
    use_custom = request.json.get("custom", False)
    persona_id = str(uuid.uuid4())

    initialize_persona(persona_id)

    if use_custom:
        _setup_history[persona_id] = []
        prompt = (
            "Hi, I'm LEAi and I'm going to help you create your ideal AI companion! "
            "Let's start with the basics — what's her name?"
        )
        _setup_history[persona_id].append({"role": "model", "parts": [prompt]})
        return jsonify({"persona_id": persona_id, "initial_prompt": prompt, "building_persona": True})

    return jsonify({"persona_id": persona_id, "initial_prompt": None, "building_persona": False})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get("message")
    persona_id = data.get("persona_id")

    if not (user_message and persona_id):
        return jsonify({"error": "Missing message or persona_id"}), 400

    # ── Custom persona setup phase ────────────────────────────────────────────
    if persona_id in _setup_history:
        _setup_history[persona_id].append({"role": "user", "parts": user_message})
        n_user = sum(1 for m in _setup_history[persona_id] if m["role"] == "user")

        if n_user <= SETUP_MAX_TURNS:
            setup_model = genai.GenerativeModel("gemini-2.5-flash-lite", system_instruction=SETUP_AGENT)
            response = setup_model.start_chat(history=_setup_history[persona_id]).send_message(user_message)
            _setup_history[persona_id].append({"role": "model", "parts": [response.text]})

            if "EXIT" not in response.text:
                return jsonify({"reply": response.text})

        # Setup complete (EXIT received or max turns reached) — build persona
        building_model = genai.GenerativeModel("gemini-2.5-flash-lite", system_instruction=BUILD_AGENT)
        persona_text = building_model.start_chat(
            history=_setup_history[persona_id]
        ).send_message(
            "Build a detailed and complete description of the AI companion from the conversation."
        ).text

        _setup_history.pop(persona_id, None)
        save_persona(persona_id, persona_text)
        return jsonify({"reply": None, "setup_complete": True})

    # ── Main chat ─────────────────────────────────────────────────────────────
    save_message(persona_id, "user", user_message)
    reply = run_companion(persona_id, user_message)
    save_message(persona_id, "model", reply)
    return jsonify({"reply": reply})


@app.route("/load_session", methods=["POST"])
def load_session():
    data = request.json
    persona_id = data.get("persona_id")

    if not persona_id:
        return jsonify({"error": "Missing persona_id"}), 400

    # Reload memories/FAISS from DB. persona_description and user_information
    # are read fresh from the DB on every chat turn (no session cache to keep
    # in sync), so we don't need to materialize them here.
    initialize_persona(persona_id)

    history = get_history(persona_id)
    formatted = [
        {
            "role": "You" if msg["role"] == "user" else "LEAi",
            "text": msg["parts"][0] if isinstance(msg["parts"], list) else msg["parts"],
        }
        for msg in history
    ]
    return jsonify({"history": formatted})


if __name__ == "__main__":
    app.run(debug=True)
