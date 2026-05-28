import os
import re
import sqlite3
from contextlib import contextmanager

import google.generativeai as genai
import numpy as np
import faiss
from langfuse import observe, get_client

from prompts import (
    RATING_AGENT_VERSIONS,
    BUILD_MEMORY,
    IS_ABOUT_COMPANION,
    IS_ABOUT_USER,
    UPDATE_PERSONA,
    UPDATE_USER_INFORMATION,
    MERGE_MEMORIES,
)

# DB path can be overridden via env var so the long-term-memory evaluation does
# not pollute the user's live chat.db.
DB_PATH = os.environ.get("LEAI_DB_PATH", "chat.db")

# Thesis-selected memory parameters.
MEMORY_SCORE_THRESHOLD = 4  # minimum memory-worthiness score (1-10) required to store
MEMORY_CONTEXT_TURNS = 6    # number of prior turns fed to the memory-scoring prompt

# Per-request timeout (seconds) for Gemini calls. The SDK default is 600s, which
# means a dropped connection blocks for 10 minutes before surfacing. A short
# timeout makes a network blip raise quickly so the eval's retry logic can recover.
GEMINI_REQUEST_TIMEOUT = int(os.environ.get("LEAI_GEMINI_TIMEOUT", "60"))
GEMINI_REQUEST_OPTIONS = {"timeout": GEMINI_REQUEST_TIMEOUT}

# Pre-instantiated Gemini models — API key configured in app.py before first call
_gemini = genai.GenerativeModel("gemini-2.5-flash-lite")
_build_memory_model = genai.GenerativeModel("gemini-2.5-flash-lite", system_instruction=BUILD_MEMORY)
_update_persona_model = genai.GenerativeModel("gemini-2.5-flash-lite", system_instruction=UPDATE_PERSONA)
_update_user_model = genai.GenerativeModel("gemini-2.5-flash-lite", system_instruction=UPDATE_USER_INFORMATION)
_merge_model = genai.GenerativeModel("gemini-2.5-flash-lite", system_instruction=MERGE_MEMORIES)


# ── Database ──────────────────────────────────────────────────────────────────

@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS chat_history (
            persona_id TEXT, role TEXT, message TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS memory (
            persona_id TEXT, memory TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS memory_embeddings (
            persona_id TEXT, memory_embedding BLOB)""")
        c.execute("""CREATE TABLE IF NOT EXISTS user_information (
            persona_id TEXT, information TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS custom_persona (
            persona_id TEXT, information TEXT)""")


def save_message(persona_id: str, role: str, message: str) -> None:
    with _db() as c:
        c.execute(
            "INSERT INTO chat_history (persona_id, role, message) VALUES (?, ?, ?)",
            (persona_id, role, message),
        )


def save_persona(persona_id: str, info: str) -> None:
    # These tables have no PRIMARY KEY, so REPLACE INTO would just append.
    # Delete any existing row for this persona first to keep exactly one.
    with _db() as c:
        c.execute("DELETE FROM custom_persona WHERE persona_id = ?", (persona_id,))
        c.execute(
            "INSERT INTO custom_persona (persona_id, information) VALUES (?, ?)",
            (persona_id, info),
        )


def save_user_info(persona_id: str, info: str) -> None:
    with _db() as c:
        c.execute("DELETE FROM user_information WHERE persona_id = ?", (persona_id,))
        c.execute(
            "INSERT INTO user_information (persona_id, information) VALUES (?, ?)",
            (persona_id, info),
        )


def save_memory(persona_id: str, memory: str) -> None:
    with _db() as c:
        c.execute(
            "INSERT INTO memory (persona_id, memory) VALUES (?, ?)",
            (persona_id, memory),
        )


def save_memory_embedding(persona_id: str, memory_embedding) -> None:
    with _db() as c:
        c.execute(
            "INSERT INTO memory_embeddings (persona_id, memory_embedding) VALUES (?, ?)",
            (persona_id, memory_embedding.astype("float32").tobytes()),
        )


def get_history(persona_id: str) -> list[dict]:
    with _db() as c:
        c.execute(
            "SELECT role, message FROM chat_history WHERE persona_id = ?",
            (persona_id,),
        )
        return [{"role": row[0], "parts": [row[1]]} for row in c.fetchall()]


def count_chat_history(persona_id: str) -> int:
    """Number of chat_history rows for a persona (used as an eval checkpoint)."""
    with _db() as c:
        c.execute("SELECT COUNT(*) FROM chat_history WHERE persona_id = ?", (persona_id,))
        return c.fetchone()[0]


def truncate_chat_history(persona_id: str, keep_count: int) -> None:
    """Keep the oldest `keep_count` chat_history rows for a persona, delete the rest.

    Used by the evaluation to roll back recall questions/replies (and any
    retry-orphaned rows) so each recall phase starts from the same
    post-build-up snapshot.
    """
    with _db() as c:
        c.execute(
            "DELETE FROM chat_history WHERE ROWID IN ("
            "SELECT ROWID FROM chat_history WHERE persona_id = ? "
            "ORDER BY ROWID ASC LIMIT -1 OFFSET ?)",
            (persona_id, keep_count),
        )


def get_memories(persona_id: str) -> list[str]:
    with _db() as c:
        c.execute("SELECT memory FROM memory WHERE persona_id = ?", (persona_id,))
        return [row[0] for row in c.fetchall()]


def get_memory_embeddings(persona_id: str, emb_dim: int) -> list:
    with _db() as c:
        c.execute(
            "SELECT memory_embedding FROM memory_embeddings WHERE persona_id = ?",
            (persona_id,),
        )
        return [
            np.frombuffer(row[0], dtype="float32").reshape(1, emb_dim)
            for row in c.fetchall()
        ]


def get_custom_persona(persona_id: str) -> str | None:
    with _db() as c:
        c.execute(
            "SELECT information FROM custom_persona WHERE persona_id = ? "
            "ORDER BY ROWID DESC LIMIT 1",
            (persona_id,),
        )
        row = c.fetchone()
        return row[0] if row else None


def get_user_info(persona_id: str) -> str | None:
    with _db() as c:
        c.execute(
            "SELECT information FROM user_information WHERE persona_id = ? "
            "ORDER BY ROWID DESC LIMIT 1",
            (persona_id,),
        )
        row = c.fetchone()
        return row[0] if row else None


def delete_memory_db(persona_id: str, i: int) -> None:
    with _db() as c:
        c.execute(
            "SELECT ROWID FROM memory WHERE persona_id = ? ORDER BY ROWID LIMIT 1 OFFSET ?",
            (persona_id, i),
        )
        row = c.fetchone()
        if row:
            c.execute("DELETE FROM memory WHERE ROWID = ?", (row[0],))


def delete_embedding_db(persona_id: str, i: int) -> None:
    with _db() as c:
        c.execute(
            "SELECT ROWID FROM memory_embeddings WHERE persona_id = ? ORDER BY ROWID LIMIT 1 OFFSET ?",
            (persona_id, i),
        )
        row = c.fetchone()
        if row:
            c.execute("DELETE FROM memory_embeddings WHERE ROWID = ?", (row[0],))


# ── Context helpers ───────────────────────────────────────────────────────────

def get_last_n_turns(persona_id: str, n: int = 5) -> list[tuple[str, str]]:
    history = get_history(persona_id)
    turns = []
    for msg in history[-2 * n:]:
        role = msg["role"]
        text = msg["parts"][0] if isinstance(msg["parts"], list) else msg["parts"]
        turns.append((role, text))
    return turns


def build_multi_turn_context(turns: list[tuple[str, str]]) -> str:
    lines = []
    for role, msg in turns:
        prefix = "AI" if role == "model" else "User"
        lines.append(f"{prefix}: {msg}")
    return "\n".join(lines)


# ── LLM functions ─────────────────────────────────────────────────────────────

@observe(name="memory_worthy_score")
def memory_worthy_score(
    user_message: str,
    persona_id: str,
    n: int = 2,
    prompt_version: str = "v1",
    context_override: str | None = None,
) -> tuple[int, str]:
    if context_override is not None:
        context = context_override
    else:
        turns = get_last_n_turns(persona_id, n)
        context = build_multi_turn_context(turns)
    prompt_template = RATING_AGENT_VERSIONS[prompt_version]
    prompt = prompt_template.format(user_message=user_message, conversation_context=context)
    response = _gemini.generate_content(prompt, request_options=GEMINI_REQUEST_OPTIONS)
    try:
        text = response.text.strip()
        match = re.search(r"\b(10|[1-9])\s*[-–—]", text)
        if not match:
            raise ValueError("no '<rating> - <justification>' pattern found")
        score = int(match.group(1))
        get_client().update_current_span(
            metadata={"score": score, "persona_id": persona_id, "prompt_version": prompt_version},
        )
        return score, text
    except Exception as e:
        print(f"Error parsing memory score: {e} | LLM output: {response.text}")
        return 0, "error"


@observe(name="build_memory")
def build_memory(user_message: str, persona_id: str, n: int = 3) -> str:
    turns = get_last_n_turns(persona_id, n)
    context = build_multi_turn_context(turns)
    msg = f"""Now extract a memory fact from the user message using the conversation context.

User message:
{user_message}

Conversation context:
{context}"""
    return _build_memory_model.generate_content(msg, request_options=GEMINI_REQUEST_OPTIONS).text.strip()


@observe(name="is_about_companion")
def is_about_companion(memory: str) -> bool:
    prompt = IS_ABOUT_COMPANION.format(memory=memory)
    return "true" in _gemini.generate_content(prompt, request_options=GEMINI_REQUEST_OPTIONS).text.strip().lower()


@observe(name="is_about_user")
def is_about_user(memory: str) -> bool:
    prompt = IS_ABOUT_USER.format(memory=memory)
    return "true" in _gemini.generate_content(prompt, request_options=GEMINI_REQUEST_OPTIONS).text.strip().lower()


@observe(name="update_persona")
def update_persona(new_fact: str, current_persona: str) -> str:
    msg = f"""Current description:
{current_persona}

New fact:
{new_fact}

Updated description:"""
    return _update_persona_model.generate_content(msg, request_options=GEMINI_REQUEST_OPTIONS).text.strip()


@observe(name="update_user_information")
def update_user_information(new_fact: str, user_information: str) -> str:
    msg = f"""Current description:
{user_information}

New fact:
{new_fact}

Updated description:"""
    return _update_user_model.generate_content(msg, request_options=GEMINI_REQUEST_OPTIONS).text.strip()


@observe(name="merge_memories")
def merge_memories(memory1: str, memory2: str) -> str:
    msg = f"""Merge the two following texts:

Information 1:
{memory1}

Information 2:
{memory2}"""
    return _merge_model.generate_content(msg, request_options=GEMINI_REQUEST_OPTIONS).text.strip()


def remove_embedding(index, i: int):
    """Rebuild the FAISS index without the embedding at position i."""
    vectors = np.vstack(
        [index.reconstruct(j) for j in range(index.ntotal)]
    ).astype("float32")
    mask = np.arange(len(vectors)) != int(i)
    new_index = faiss.IndexFlatIP(index.d)
    if mask.any():
        new_index.add(vectors[mask])
    return new_index
