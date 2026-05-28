"""
Thesis A/B evaluation of the memory-worthiness rating prompt (RATING_AGENT_V1 vs RATING_AGENT_V2).

Runs a labeled dataset of user messages through `memory_worthy_score` for both
prompt versions (v1: verbose few-shot, v2: stripped zero-shot CoT) and reports
accuracy / precision / recall / F1, plus a per-category breakdown and a list
of messages where the two versions disagree.

Every call is traced in Langfuse and tagged with `rating_agent_v1` /
`rating_agent_v2`, so traces can be filtered and compared in the dashboard.
"""

import os
import re
import sys
import time

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

RATE_LIMIT_SLEEP_SEC = 0.5   # small pacing only; paid-tier RPM is in the thousands
MAX_RETRIES = 3              # max 429 retries per call
RETRY_FALLBACK_SLEEP_SEC = 60.0

import google.generativeai as genai
genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))

from google.api_core.exceptions import ResourceExhausted

from langfuse import get_client, propagate_attributes
from helpers import memory_worthy_score, MEMORY_SCORE_THRESHOLD


def _scored_with_retry(prompt_version: str, row: dict) -> tuple[int, str]:
    """Call memory_worthy_score, retrying on 429 ResourceExhausted."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return memory_worthy_score(
                user_message=row["message"],
                persona_id=f"eval_{prompt_version}",
                prompt_version=prompt_version,
                context_override=row["context"],
            )
        except ResourceExhausted as e:
            wait = RETRY_FALLBACK_SLEEP_SEC
            match = re.search(r"retry in ([\d.]+)s", str(e))
            if match:
                wait = float(match.group(1)) + 2.0
            print(f"    [rate-limited] attempt {attempt}/{MAX_RETRIES}, sleeping {wait:.0f}s...")
            time.sleep(wait)
    raise RuntimeError(f"Exhausted {MAX_RETRIES} retries for row id={row['id']}")


# ── Labeled dataset ───────────────────────────────────────────────────────────
# 100 sentences carried over from the original Memory_worthy_test.py dataset
# (50 memory-worthy + 50 not). Used here for continuity with prior thesis work.
# expected_label = True  → message should be stored as memory
# expected_label = False → message should be skipped
# No context is provided (these sentences are standalone), so context_override="".

MEMORY_WORTHY = [
    "My sister's name is Claire.",
    "I just got promoted to project manager at work.",
    "I'm allergic to peanuts.",
    "My favorite book is The Hobbit.",
    "I'll be in Rome next weekend for a conference.",
    "My best friend Alex just moved to Berlin to start a new job in a design studio, which has been his dream since college.",
    "I'm planning to quit smoking this year.",
    "My birthday is on November 12th.",
    "I met my partner during a hiking trip in the Alps back in 2019, and we've been together ever since.",
    "I'm going to start learning Spanish next month.",
    "I hate loud restaurants, they stress me out.",
    "Ever since college, I've loved collecting vintage postcards from different countries I visit.",
    "My dog Max is turning 5 years old tomorrow.",
    "I moved to Paris last summer after getting accepted into a master's program.",
    "My cousin Laura just had a baby girl named Sophie.",
    "I'll have lunch with my mom on Sunday.",
    "I wear glasses when I read.",
    "I once traveled alone across Japan for a month, visiting Kyoto, Osaka, and small rural villages.",
    "Please remember that I prefer tea over coffee.",
    "I'm going to a Taylor Swift concert next July.",
    "I hate being late, it stresses me out.",
    "I lost my phone yesterday but found it under the couch after hours of searching.",
    "My hometown is Lyon.",
    "I learned to swim when I was 4 years old.",
    "I'll meet the client at 3pm tomorrow.",
    "My brother is studying medicine in Canada.",
    "I just bought a red bicycle.",
    "My favorite color has always been green.",
    "I'm going to start running every morning to prepare for a local 10k race.",
    "I once met the president at a charity gala in Paris, and we spoke for a few minutes about climate change.",
    "I sprained my ankle while playing soccer last weekend.",
    "I love cooking Italian food on weekends, especially pasta dishes from scratch.",
    "Remember that my work schedule changed to 9am-5pm.",
    "I'm going to volunteer at the animal shelter next Saturday.",
    "I broke my arm when I was 10.",
    "I'm scared of heights.",
    "My favorite singer is Adele.",
    "Not a fan of crowded places, it's too much for me to handle",
    "My partner and I celebrated our 5th anniversary yesterday with a surprise weekend getaway.",
    "I studied computer science at university.",
    "My father's birthday is on April 2nd.",
    "I'm vegetarian and don't eat meat.",
    "Ever since I was little, hanging at the skatepark has been a huge part of every summer.",
    "My cousin Mark lives in Sydney, Australia, and we talk every Sunday morning.",
    "I plan to visit New York next spring for a week-long photography workshop.",
    "My favorite hobby is painting landscapes.",
    "I'm fluent in French and Spanish.",
    "I once won a national chess competition when I was 14 years old.",
    "I have two younger brothers named Ethan and Leo.",
    "I'm training for my first marathon in October.",
]

NOT_MEMORY_WORTHY = [
    "It's raining again outside.",
    "I had cereal for breakfast.",
    "The movie last night was boring.",
    "Just finished doing the dishes.",
    "I burned my toast again.",
    "I'm feeling a bit tired today.",
    "The train was 15 minutes late.",
    "I forgot to buy bread.",
    "The park was really crowded this morning.",
    "I'll water the plants later.",
    "I'm watching TV right now.",
    "I stubbed my toe on the coffee table.",
    "The Wi-Fi was a bit slow earlier.",
    "I just opened the window for some fresh air.",
    "I sneezed three times in a row.",
    "The elevator is out of service today.",
    "I spilled some water on the counter.",
    "The neighbor's dog was barking all morning.",
    "I saw a bird on the balcony.",
    "I had a cup of coffee an hour ago.",
    "I'm scrolling through social media.",
    "There's a small scratch on my phone screen.",
    "The traffic lights were slow to change today.",
    "I bought a pack of chewing gum.",
    "The street outside is noisy right now.",
    "I just closed the curtains.",
    "The clouds look dark and heavy.",
    "I sneezed when I went outside.",
    "My phone battery is almost full.",
    "I had pasta for lunch.",
    "The kettle took forever to boil.",
    "The bus was half empty this morning.",
    "I dropped my pen under the desk.",
    "The fridge light flickered once.",
    "I yawned twice while reading.",
    "The wind is making the window rattle.",
    "I poured myself a glass of water.",
    "The leaves are falling from the tree outside.",
    "My shoes feel a bit tight today.",
    "The store ran out of my favorite brand of soap.",
    "I'm going to open the window for some fresh air.",
    "I changed the bedsheets yesterday.",
    "I'm wearing my blue socks today.",
    "I left the TV remote on the couch.",
    "The paper jammed in the printer earlier.",
    "I heard a car alarm outside.",
    "I'm chewing gum right now.",
    "There's a small puddle near the bus stop.",
    "I closed the door behind me.",
    "I adjusted my chair before sitting down.",
]

EVAL_DATASET: list[dict] = (
    [{"id": i + 1, "message": m, "context": "", "expected_label": True, "category": "meaningful"}
     for i, m in enumerate(MEMORY_WORTHY)]
    + [{"id": len(MEMORY_WORTHY) + i + 1, "message": m, "context": "", "expected_label": False, "category": "trivial"}
       for i, m in enumerate(NOT_MEMORY_WORTHY)]
)


# ── Eval loop ─────────────────────────────────────────────────────────────────

def evaluate(prompt_version: str) -> list[dict]:
    results = []
    tag = f"rating_agent_{prompt_version}"
    total = len(EVAL_DATASET)
    for i, row in enumerate(EVAL_DATASET, start=1):
        with propagate_attributes(tags=[tag], metadata={"eval_row_id": str(row["id"])}):
            score, justification = _scored_with_retry(prompt_version, row)
        predicted = score >= MEMORY_SCORE_THRESHOLD
        results.append({
            **row,
            "score": score,
            "predicted_label": predicted,
            "correct": predicted == row["expected_label"],
            "justification": justification,
        })
        print(f"  [{prompt_version}] {i}/{total}  id={row['id']:2}  score={score:2}  expected={row['expected_label']}")
        if i < total:
            time.sleep(RATE_LIMIT_SLEEP_SEC)
    return results


# ── Metrics ───────────────────────────────────────────────────────────────────

def metrics(results: list[dict]) -> dict:
    tp = sum(1 for r in results if r["predicted_label"] and r["expected_label"])
    fp = sum(1 for r in results if r["predicted_label"] and not r["expected_label"])
    fn = sum(1 for r in results if not r["predicted_label"] and r["expected_label"])
    tn = sum(1 for r in results if not r["predicted_label"] and not r["expected_label"])
    n = len(results)
    accuracy = (tp + tn) / n if n else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def category_breakdown(results: list[dict]) -> dict:
    out = {}
    for cat in ("trivial", "meaningful"):
        cat_results = [r for r in results if r["category"] == cat]
        correct = sum(1 for r in cat_results if r["correct"])
        out[cat] = (correct, len(cat_results))
    return out


def disagreements(v1: list[dict], v2: list[dict]) -> list[dict]:
    diffs = []
    for r1, r2 in zip(v1, v2):
        if r1["predicted_label"] != r2["predicted_label"]:
            diffs.append({
                "id": r1["id"],
                "message": r1["message"],
                "expected": r1["expected_label"],
                "v1_score": r1["score"],
                "v1_pred": r1["predicted_label"],
                "v2_score": r2["score"],
                "v2_pred": r2["predicted_label"],
            })
    return diffs


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_metrics(label: str, m: dict) -> None:
    print(f"  {label:4} | acc={m['accuracy']:.1%}  prec={m['precision']:.1%}  rec={m['recall']:.1%}  f1={m['f1']:.1%}  "
          f"(TP={m['tp']} FP={m['fp']} FN={m['fn']} TN={m['tn']})")


def print_categories(label: str, cats: dict) -> None:
    parts = [f"{cat}={c}/{t}" for cat, (c, t) in cats.items()]
    print(f"  {label:4} | " + "   ".join(parts))


def main() -> None:
    # CLI: `py eval_rating_agent.py [v1] [v2]` — defaults to both versions.
    versions = [v for v in sys.argv[1:] if v in ("v1", "v2")] or ["v1", "v2"]
    print(f"Running A/B eval on {len(EVAL_DATASET)} messages  (versions: {versions})\n")

    results: dict[str, list[dict]] = {}
    for i, v in enumerate(versions):
        print(f"-> Evaluating {v} ({'verbose few-shot' if v == 'v1' else 'zero-shot CoT'})...")
        if i > 0:
            time.sleep(RATE_LIMIT_SLEEP_SEC)
        results[v] = evaluate(v)
        print()

    print("=" * 88)
    print("Overall metrics")
    print("=" * 88)
    for v in versions:
        print_metrics(v, metrics(results[v]))

    print()
    print("=" * 88)
    print("Per-category accuracy")
    print("=" * 88)
    for v in versions:
        print_categories(v, category_breakdown(results[v]))

    if "v1" in results and "v2" in results:
        print()
        print("=" * 88)
        print("Disagreements (v1 vs v2)")
        print("=" * 88)
        diffs = disagreements(results["v1"], results["v2"])
        if not diffs:
            print("  (none — both versions agree on every message)")
        else:
            for d in diffs:
                exp = "STORE" if d["expected"] else "skip "
                v1_tag = "STORE" if d["v1_pred"] else "skip "
                v2_tag = "STORE" if d["v2_pred"] else "skip "
                print(f"  #{d['id']:2}  expected={exp}  "
                      f"v1={d['v1_score']:2}({v1_tag})  v2={d['v2_score']:2}({v2_tag})  "
                      f"msg=\"{d['message'][:55]}\"")

    print()
    print("See https://cloud.langfuse.com -> Tracing -> filter tags `rating_agent_v1` / `rating_agent_v2`")
    print("Inspect any trace to compare full prompt input, model output, latency, and token cost.")

    get_client().flush()


if __name__ == "__main__":
    main()
