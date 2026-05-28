"""
LEAi companion engine (direct google-genai, no ADK).

This module replaces the previous ADK-based two-agent setup with a single,
deterministic Python flow:

    run_companion(persona_id, message)
        1. Score + extract + store the message's memory (always runs).
        2. Optionally retrieve relevant memories from FAISS (if RAG_ENABLED).
        3. Build a prompt: companion personality + user profile + retrieved
           memories + (token-capped) recent chat history + new user message.
        4. Call gemini-2.5-flash-lite once; return the text reply.

Module-level state preserved from the previous design:
  _embedder   – sentence-transformers model used for memory embeddings + RAG
  _memories   – per-persona in-memory mirror of the `memory` SQLite table
  _emb_index  – per-persona FAISS index, rebuilt on initialize_persona

The eval runner (evaluation/run_long_term_memory_eval.py) toggles RAG_ENABLED
and reads _memories / _emb_index for its probe; both are kept for compatibility.
"""

import os

import google.generativeai as genai

from embedder import Embedder
from helpers import (
    MEMORY_SCORE_THRESHOLD,
    MEMORY_CONTEXT_TURNS,
    GEMINI_REQUEST_OPTIONS,
    memory_worthy_score,
    build_memory,
    is_about_companion,
    is_about_user,
    update_persona,
    update_user_information,
    merge_memories,
    get_memories,
    get_memory_embeddings,
    get_history,
    get_custom_persona,
    get_user_info,
    save_memory,
    save_memory_embedding,
    save_persona,
    save_user_info,
    delete_memory_db,
    delete_embedding_db,
    remove_embedding,
)
from prompts import LEAI_PERSONALITY

SIMILARITY_MERGE_THRESHOLD = 0.85
# Tuned for sentence-transformers/all-MiniLM-L6-v2: question-to-fact pairs
# typically score 0.3-0.5 even when clearly about the same topic, so 0.6 is too
# strict. 0.35 lets topically-related memories through while still filtering
# obvious noise.
SIMILARITY_RETRIEVAL_THRESHOLD = 0.35

# Token cap for the recent-chat-history portion of the companion's prompt.
# Default effectively unlimited (production behavior unchanged); the
# long-term-memory evaluation sets a tight value via LEAI_CONTEXT_CAP_TOKENS to
# simulate a cost-bounded context window.
CONTEXT_CAP_TOKENS = int(os.environ.get("LEAI_CONTEXT_CAP_TOKENS", "10000000"))

# Toggle used by the evaluation to compare the "no RAG" (B) and "with RAG" (C)
# regimes. When False, retrieve_memories returns an empty list — the companion
# answers from persona/user-profile context alone.
RAG_ENABLED = True

# Toggle used by the evaluation's recall phase: when False, run_companion skips
# the memory-writing pipeline so a recall question doesn't mutate the persona's
# stored state. This keeps every recall question evaluated against the same
# post-build-up memory snapshot. Always True in production.
MEMORY_WRITE_ENABLED = True

# Number of top-k semantic matches to consider when RAG is enabled.
RAG_TOP_K = 5

COMPANION_MODEL_NAME = "gemini-2.5-flash-lite"

# ── Module-level in-memory state ──────────────────────────────────────────────
_embedder = Embedder("sentence-transformers/all-MiniLM-L6-v2")
_memories: dict[str, list[str]] = {}
_emb_index: dict = {}


def _estimate_tokens(text: str) -> int:
    """Rough token estimate at ~4 chars per token."""
    return len(text) // 4 + 1


def initialize_persona(persona_id: str) -> None:
    """Load a persona's stored memories from SQLite into in-memory state."""
    _memories[persona_id] = get_memories(persona_id)
    _emb_index[persona_id] = _embedder.faiss_index()
    for emb in get_memory_embeddings(persona_id, _embedder.dimension()):
        _emb_index[persona_id].add(emb)


# ── Memory pipeline (deterministic, runs every turn) ─────────────────────────

def process_message_memory(persona_id: str, message: str) -> str:
    """Score → extract → classify → persist. Runs on every user message.

    Returns a one-line description of what happened (for logging / debugging).
    """
    if not persona_id:
        return "No active session."

    score, _ = memory_worthy_score(message, persona_id, n=MEMORY_CONTEXT_TURNS)
    if score < MEMORY_SCORE_THRESHOLD:
        return f"Not memory-worthy (score {score})."

    memory = build_memory(message, persona_id, n=3)
    if "NONE" in memory.strip().upper():
        return "No extractable fact found."

    if is_about_companion(memory):
        current = get_custom_persona(persona_id) or ""
        updated = update_persona(memory, current)
        save_persona(persona_id, updated)
        return f"Companion persona updated: {memory}"

    if is_about_user(memory):
        current = get_user_info(persona_id) or ""
        updated = update_user_information(memory, current)
        save_user_info(persona_id, updated)
        return f"User profile updated: {memory}"

    # Generic third-party / external fact → memory table + FAISS.
    mems = _memories.get(persona_id, [])
    idx = _emb_index.get(persona_id)

    if mems and idx and idx.ntotal > 0:
        D, I = _embedder.similarity_search(memory, idx)
        if D[0] > SIMILARITY_MERGE_THRESHOLD:
            memory = merge_memories(mems[int(I[0])], memory)
            _emb_index[persona_id] = remove_embedding(idx, int(I[0]))
            _memories[persona_id].pop(int(I[0]))
            delete_embedding_db(persona_id, int(I[0]))
            delete_memory_db(persona_id, int(I[0]))

    embedding = _embedder.encode([memory])
    _memories[persona_id].append(memory)
    _emb_index[persona_id].add(embedding)
    save_memory(persona_id, memory)
    save_memory_embedding(persona_id, embedding)
    return f"Memory stored: {memory}"


# ── RAG retrieval ────────────────────────────────────────────────────────────

def retrieve_memories(persona_id: str, query: str) -> list[str]:
    """Return up to RAG_TOP_K memories whose similarity to `query` exceeds
    SIMILARITY_RETRIEVAL_THRESHOLD. Empty list if RAG is disabled or no match.
    """
    if not RAG_ENABLED:
        return []
    if not persona_id:
        return []

    mems = _memories.get(persona_id, [])
    idx = _emb_index.get(persona_id)
    if not mems or idx is None or idx.ntotal == 0:
        return []

    k = min(RAG_TOP_K, len(mems))
    D, I = _embedder.similarity_search(query, idx, k=k)
    return [
        mems[int(i)]
        for d, i in zip(D, I)
        if d > SIMILARITY_RETRIEVAL_THRESHOLD and 0 <= int(i) < len(mems)
    ]


# ── Prompt building with token-capped history ────────────────────────────────

def _capped_history(persona_id: str, exclude_last_user_message: str) -> list[dict]:
    """Return the recent chat history as a list of {role, parts} dicts, trimmed
    from the oldest end until total tokens fit under CONTEXT_CAP_TOKENS.

    Excludes the very last user message (which is passed to the model
    separately as `new_message`) to avoid double-sending it.
    """
    history = get_history(persona_id)
    if history and history[-1].get("role") == "user":
        last = history[-1]
        last_text = last["parts"][0] if isinstance(last["parts"], list) else last["parts"]
        if last_text == exclude_last_user_message:
            history = history[:-1]

    # Walk newest -> oldest, accumulate tokens, drop oldest when budget exceeded.
    # History is chronological so we walk in reverse.
    kept_reversed: list[dict] = []
    total = 0
    for entry in reversed(history):
        parts = entry.get("parts") or []
        text = parts[0] if isinstance(parts, list) and parts else ""
        t = _estimate_tokens(text)
        if kept_reversed and total + t > CONTEXT_CAP_TOKENS:
            break
        kept_reversed.append(entry)
        total += t
    return list(reversed(kept_reversed))


def _build_companion_prompt(
    persona_id: str,
    user_message: str,
    retrieved: list[str],
) -> tuple[str, list[dict]]:
    """Return (system_instruction, contents) for the genai call.

    `system_instruction` carries persona, user profile and retrieved memories.
    `contents` is the recent capped history plus the new user turn, in the
    {role, parts} dict shape google-generativeai accepts.
    """
    persona_description = get_custom_persona(persona_id) or LEAI_PERSONALITY
    user_information = get_user_info(persona_id) or ""

    retrieval_block = ""
    if retrieved:
        retrieval_block = (
            "\n\nRelevant facts retrieved from long-term memory (use these if they "
            "help you answer the user):\n"
            + "\n".join(f"- {m}" for m in retrieved)
        )

    system_instruction = (
        "You are LEAi, an AI companion. Reply naturally — warm, personal, "
        "concise, in character.\n\n"
        f"Personality:\n{persona_description}\n\n"
        f"User profile:\n{user_information}"
        f"{retrieval_block}"
    )

    history = _capped_history(persona_id, exclude_last_user_message=user_message)
    contents = list(history) + [{"role": "user", "parts": [user_message]}]
    return system_instruction, contents


# ── Public entry point: the only thing app.py needs to call ──────────────────

def run_companion(persona_id: str, message: str) -> str:
    """Full companion turn: memory pipeline → (optional) RAG → LLM reply.

    Synchronous; one Gemini call for the reply (plus the internal helpers used
    by process_message_memory). Returns the model's text response.
    """
    if not persona_id:
        return ""

    # 1. Deterministic memory pipeline. Updates persona_description /
    #    user_information / memory store as appropriate. Never decided by the LLM.
    #    Skipped when MEMORY_WRITE_ENABLED is False (evaluation recall phase).
    if MEMORY_WRITE_ENABLED:
        process_message_memory(persona_id, message)

    # 2. Deterministic RAG retrieval. Returns [] if RAG_ENABLED is False.
    retrieved = retrieve_memories(persona_id, message)

    # 3. Build the prompt (system instruction + capped history + new turn).
    system_instruction, contents = _build_companion_prompt(persona_id, message, retrieved)

    # 4. One LLM call. No agent transfer, no event filtering, no empty replies.
    model = genai.GenerativeModel(
        COMPANION_MODEL_NAME,
        system_instruction=system_instruction,
    )
    response = model.generate_content(contents, request_options=GEMINI_REQUEST_OPTIONS)

    # google-generativeai returns a Response; .text is the concatenated text.
    try:
        return (response.text or "").strip()
    except Exception:
        # If the response has no text parts (rare; usually a safety block),
        # surface an empty string and let the caller decide what to do.
        return ""
