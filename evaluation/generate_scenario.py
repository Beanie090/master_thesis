"""
Generate a long balanced long-term-memory scenario for the LEAi evaluation.

Produces a single scenario with 30 fact topics (24 third-party + 6 user-self),
each introduced over 3 messages, interspersed with distractor turns and followed
by a heavy distractor tail so every fact is evicted from the context cap by
recall time. Writes evaluation/scenarios.json.

Run:  python evaluation/generate_scenario.py
"""

import json
import random
from pathlib import Path

random.seed(42)  # deterministic output

# ── Facts ─────────────────────────────────────────────────────────────────────
# Each fact: id, type, 3 build-up messages, a recall question, and the
# expected keyword groups the grader checks the answer against.

FACTS = [
    # ── User-self (6) ──
    {
        "id": "birthday", "type": "user_self",
        "messages": [
            "By the way, I want you to remember my birthday.",
            "My birthday is in April.",
            "Specifically, it's on April 25.",
        ],
        "question": "Do you remember when my birthday is?",
        "expected": [["april"], ["25", "25th", "twenty-fifth", "twenty fifth"]],
    },
    {
        "id": "food", "type": "user_self",
        "messages": [
            "I'm quite particular when it comes to food.",
            "I really dislike mushrooms in any dish.",
            "But I absolutely love spicy ramen, especially with a rich broth.",
        ],
        "question": "What food do I dislike, and what do I love?",
        "expected": [["dislike", "hate", "don't like", "do not like", "not a fan"], ["mushroom", "mushrooms"], ["love", "like", "enjoy"], ["ramen"]],
    },
    {
        "id": "gym", "type": "user_self",
        "messages": [
            "My weekly gym routine has become pretty stable.",
            "I go to the gym every Monday, Wednesday, and Friday.",
            "I always go in the evening, after work.",
        ],
        "question": "What days and time do I go to the gym?",
        "expected": [["monday"], ["wednesday"], ["friday"], ["evening", "evenings"]],
    },
    {
        "id": "thesis", "type": "user_self",
        "messages": [
            "I'm currently writing my master's thesis.",
            "It's about AI companion chatbots that have long-term conversational memory.",
            "It also studies how the chatbot's personality can evolve and adapt to the user over time.",
        ],
        "question": "What is my master's thesis about?",
        "expected": [["ai companion", "companion chatbot", "chatbot"], ["long-term memory", "long term memory", "memory"], ["personality", "evolve", "adapt"]],
    },
    {
        "id": "language", "type": "user_self",
        "messages": [
            "I've been trying to pick up a new language.",
            "I'm learning Japanese.",
            "I usually study it for half an hour every morning before work.",
        ],
        "question": "Which language am I learning, and when do I study it?",
        "expected": [["japanese"], ["morning", "mornings"]],
    },
    {
        "id": "job", "type": "user_self",
        "messages": [
            "Let me tell you a bit about my work.",
            "I work remotely as a backend software developer.",
            "I mostly write Python and work with databases all day.",
        ],
        "question": "What do I do for work?",
        "expected": [["backend", "software", "developer", "programmer"], ["remote", "remotely"]],
    },

    # ── Third-party (24) ──
    {
        "id": "noemie", "type": "third_party",
        "messages": [
            "Let me tell you about my close friend Noémie.",
            "Noémie is a 28-year-old artist who lives in Lyon.",
            "She specializes in watercolor landscapes of the French countryside.",
        ],
        "question": "What do you remember about my friend Noémie?",
        "expected": [["noémie", "noemie"], ["lyon"], ["artist", "paint", "watercolor", "watercolour"]],
    },
    {
        "id": "grandmother", "type": "third_party",
        "messages": [
            "I want to tell you about my grandmother.",
            "Her name is Marie and she lives in a small village in Brittany.",
            "She was a school teacher for over thirty years and now tends a rose garden.",
        ],
        "question": "What do you remember about my grandmother Marie?",
        "expected": [["marie"], ["brittany"], ["teacher", "taught"], ["rose", "garden"]],
    },
    {
        "id": "anna", "type": "third_party",
        "messages": [
            "My friend Anna has a particular sensitivity I should mention.",
            "Anna strongly dislikes crowded places.",
            "She finds them overwhelming, especially when there's a lot of noise.",
        ],
        "question": "What kind of place does my friend Anna dislike, and why?",
        "expected": [["crowded", "crowds", "busy"], ["overwhelming", "noise", "noisy", "too much"]],
    },
    {
        "id": "marc", "type": "third_party",
        "messages": [
            "My friend Marc is really into running.",
            "Marc runs every weekend along the river path in our town.",
            "He usually covers about 10 kilometers each weekend.",
        ],
        "question": "What are my friend Marc's running habits?",
        "expected": [["run", "runs", "running"], ["weekend", "weekends"], ["river"], ["10", "ten"]],
    },
    {
        "id": "film", "type": "third_party",
        "messages": [
            "There's a film I keep wanting to recommend.",
            "It's called The Grand Budapest Hotel, directed by Wes Anderson.",
            "It's famous for its pastel color palette and symmetrical compositions.",
        ],
        "question": "Which film did I recommend, and what is it known for?",
        "expected": [["grand budapest", "budapest"], ["wes anderson", "anderson"], ["pastel", "symmetrical", "color palette", "composition"]],
    },
    {
        "id": "music", "type": "third_party",
        "messages": [
            "There's an artist I want to tell you about: Lana Del Rey.",
            "Lana Del Rey's music has a nostalgic, cinematic quality.",
            "Her songs are known for hazy vocals and a melancholic, dreamy atmosphere.",
        ],
        "question": "Which artist did I tell you about, and what is her sound like?",
        "expected": [["lana del rey", "lana"], ["nostalgic", "cinematic", "melancholic", "dreamy"]],
    },
    {
        "id": "brother", "type": "third_party",
        "messages": [
            "Let me tell you about my older brother.",
            "His name is Thomas and he works as an architect.",
            "He's been living in Berlin for the past five years.",
        ],
        "question": "What does my brother Thomas do, and where does he live?",
        "expected": [["thomas"], ["architect"], ["berlin"]],
    },
    {
        "id": "book", "type": "third_party",
        "messages": [
            "I read a book recently that really stuck with me.",
            "It's called The Midnight Library, by Matt Haig.",
            "It's about a woman who explores the different lives she could have lived.",
        ],
        "question": "What book did I mention, and what is it about?",
        "expected": [["midnight library"], ["matt haig", "haig"], ["different lives", "parallel lives", "could have lived", "other lives", "regret"]],
    },
    {
        "id": "coworker", "type": "third_party",
        "messages": [
            "There's a coworker of mine I should mention.",
            "His name is David and he's a strict vegetarian.",
            "He's also seriously allergic to peanuts, so we're careful at team lunches.",
        ],
        "question": "What should I keep in mind about my coworker David's diet?",
        "expected": [["david"], ["vegetarian"], ["peanut", "peanuts"], ["allerg"]],
    },
    {
        "id": "uncle", "type": "third_party",
        "messages": [
            "My uncle has an interesting life I'd love to share.",
            "His name is Pierre and he owns a vineyard.",
            "The vineyard is in the Bordeaux region and produces red wine.",
        ],
        "question": "What does my uncle Pierre do, and where?",
        "expected": [["pierre"], ["vineyard", "wine"], ["bordeaux"]],
    },
    {
        "id": "neighbor", "type": "third_party",
        "messages": [
            "Let me tell you about my neighbor.",
            "Mrs. Chen is a piano teacher who lives next door.",
            "She has twin daughters who are about ten years old.",
        ],
        "question": "Who is my neighbor Mrs. Chen, and what's notable about her family?",
        "expected": [["chen"], ["piano"], ["twin", "twins"], ["daughter", "daughters"]],
    },
    {
        "id": "restaurant", "type": "third_party",
        "messages": [
            "There's a restaurant I want to recommend to you.",
            "It's called Le Petit Jardin and it's near the river.",
            "They serve the best French onion soup I've ever had.",
        ],
        "question": "What restaurant did I recommend, and what's their best dish?",
        "expected": [["petit jardin", "jardin"], ["onion soup", "french onion"]],
    },
    {
        "id": "sofia", "type": "third_party",
        "messages": [
            "My friend Sofia is doing something I really admire.",
            "She's studying medicine in Barcelona.",
            "She wants to become a pediatrician and work with children.",
        ],
        "question": "What is my friend Sofia studying, and what's her goal?",
        "expected": [["sofia"], ["medicine", "medical"], ["barcelona"], ["pediatric", "pediatrician", "children", "kids"]],
    },
    {
        "id": "dog", "type": "third_party",
        "messages": [
            "I want to tell you about my friend's dog.",
            "His name is Biscuit and he's a golden retriever.",
            "Biscuit absolutely loves swimming in the lake.",
        ],
        "question": "Tell me about the dog Biscuit.",
        "expected": [["biscuit"], ["golden retriever", "retriever", "golden"], ["swim", "swimming", "lake", "water"]],
    },
    {
        "id": "band", "type": "third_party",
        "messages": [
            "There's a band I've been listening to a lot.",
            "They're called Phoenix, a French indie pop band.",
            "They're originally from Versailles.",
        ],
        "question": "What band did I mention, and where are they from?",
        "expected": [["phoenix"], ["indie", "pop"], ["versailles", "french", "france"]],
    },
    {
        "id": "cousin", "type": "third_party",
        "messages": [
            "My cousin has a job that lets him travel a lot.",
            "His name is Lucas and he's a professional photographer.",
            "He travels constantly, mostly shooting wildlife in remote places.",
        ],
        "question": "What does my cousin Lucas do for a living?",
        "expected": [["lucas"], ["photographer", "photography"], ["travel", "wildlife"]],
    },
    {
        "id": "cafe", "type": "third_party",
        "messages": [
            "There's a café I go to often that I should tell you about.",
            "It's called Brew & Bean and it has the best espresso in town.",
            "It's open until midnight, which is great for late evenings.",
        ],
        "question": "What café did I mention, and what's special about it?",
        "expected": [["brew & bean", "brew and bean", "brew"], ["espresso", "coffee"], ["midnight", "late"]],
    },
    {
        "id": "teacher", "type": "third_party",
        "messages": [
            "I want to tell you about a teacher who influenced me.",
            "Mr. Dubois taught history and recently retired last year.",
            "He's an avid chess player and used to run the school chess club.",
        ],
        "question": "Who was Mr. Dubois, and what are his interests?",
        "expected": [["dubois"], ["history"], ["chess"]],
    },
    {
        "id": "game", "type": "third_party",
        "messages": [
            "There's a video game I've been completely absorbed in.",
            "It's called Hollow Knight, a metroidvania.",
            "It's known for its beautiful hand-drawn art style.",
        ],
        "question": "What video game did I mention, and what's it known for?",
        "expected": [["hollow knight"], ["metroidvania", "platformer"], ["hand-drawn", "hand drawn", "art"]],
    },
    {
        "id": "sister", "type": "third_party",
        "messages": [
            "Let me tell you about my sister.",
            "Her name is Camille and she lives in Montreal.",
            "She works as a veterinarian at a small animal clinic.",
        ],
        "question": "What does my sister Camille do, and where does she live?",
        "expected": [["camille"], ["montreal"], ["veterinarian", "vet", "animal"]],
    },
    {
        "id": "colleague_trip", "type": "third_party",
        "messages": [
            "A colleague of mine just got back from an amazing trip.",
            "Her name is Yuki and she went hiking in Patagonia.",
            "She spent two weeks trekking and camping near the glaciers.",
        ],
        "question": "Where did my colleague Yuki travel, and what did she do there?",
        "expected": [["yuki"], ["patagonia"], ["hiking", "trekking", "trek", "camping"]],
    },
    {
        "id": "plant", "type": "third_party",
        "messages": [
            "My friend gave me a plant that I should tell you about.",
            "It's a fiddle-leaf fig tree named Frank.",
            "It needs bright indirect light and water once a week.",
        ],
        "question": "What kind of plant is Frank, and how do I care for it?",
        "expected": [["fiddle-leaf", "fiddle leaf", "fig"], ["indirect light", "bright"], ["once a week", "weekly", "water"]],
    },
    {
        "id": "podcast", "type": "third_party",
        "messages": [
            "There's a podcast I think you'd find interesting.",
            "It's called Deep Time and it's about geology and Earth's history.",
            "The host is a paleontologist named Dr. Reed.",
        ],
        "question": "What podcast did I mention, and who hosts it?",
        "expected": [["deep time"], ["geology", "earth", "paleonto"], ["reed"]],
    },
    {
        "id": "startup", "type": "third_party",
        "messages": [
            "A friend of mine recently started a company I want to tell you about.",
            "Her name is Elena and she founded a startup that makes eco-friendly packaging.",
            "The company is based in Amsterdam and has about twenty employees.",
        ],
        "question": "What is my friend Elena's company about, and where is it based?",
        "expected": [["elena"], ["packaging", "eco", "sustainable", "eco-friendly"], ["amsterdam"]],
    },
]



# ── Distractors ─────────────────────────────────────────────────────────────
# Template-generated for variety, plus a fixed pool. These are mundane, fact-free
# chit-chat that should NOT be stored as memories.

_SUBJECTS = ["I", "I think I", "Honestly I", "Lately I", "Today I", "This morning I"]
_PREDICATES = [
    "had a pretty quiet day", "drank too much coffee", "felt a bit tired",
    "watched a random video online", "tidied up my desk a little",
    "took a short walk outside", "couldn't decide what to eat",
    "listened to some background music", "scrolled on my phone for a while",
    "rearranged a few things in my room", "forgot where I put my keys",
    "found the weather a bit grey", "wanted to do nothing in particular",
    "felt like the day went by fast", "noticed my plant needs dusting",
    "kept yawning for no reason", "thought the office was quiet",
    "had a slightly bitter cup of tea", "saw a funny meme",
    "couldn't focus on reading", "felt like reorganizing my files",
    "had trouble picking a movie", "left a few messages unanswered",
    "kept hearing a noise outside", "wished the day were a bit longer",
]
_TAILS = [
    ".", ", nothing special.", ", you know how it is.", ", as usual.",
    " for a bit.", ", which was fine.", ", honestly.", " today.",
]

_FIXED_DISTRACTORS = [
    "How's your day going?",
    "The weather has been unpredictable lately.",
    "I should probably go to bed earlier tonight.",
    "Do you ever think about how fast time goes?",
    "I like when a room has warm lighting.",
    "Sometimes silence is underrated.",
    "I keep meaning to clean my keyboard.",
    "There's a bird that visits my window sometimes.",
    "I wonder if I should repaint my room.",
    "Mondays always feel a little slow.",
    "I enjoy a good cup of tea in the afternoon.",
    "My phone battery drains so quickly these days.",
    "I saw a really clean-looking website design today.",
    "It's nice to just chat about nothing sometimes.",
    "I might try a new recipe this week, who knows.",
]


def make_distractors(n: int) -> list[str]:
    out = list(_FIXED_DISTRACTORS)
    seen = set(out)
    while len(out) < n:
        s = f"{random.choice(_SUBJECTS)} {random.choice(_PREDICATES)}{random.choice(_TAILS)}"
        if s not in seen:
            out.append(s)
            seen.add(s)
    random.shuffle(out)
    return out[:n]


def build_scenario() -> dict:
    facts = list(FACTS)
    random.shuffle(facts)  # mix user-self and third-party throughout

    # Layout: an opening distractor block, fact clusters with a few distractors
    # between them (NOT after the last one), then a fixed-length distractor tail
    # so every fact is evicted from the context cap by recall time.
    inter_distractors = 4          # distractors between fact clusters
    tail_distractors = 67          # distractor tail after the last fact (turns)

    total_distractors = inter_distractors + inter_distractors * (len(facts) - 1) + tail_distractors
    pool = make_distractors(total_distractors + 10)
    di = 0

    messages = []

    def add_distractors(k):
        nonlocal di
        for _ in range(k):
            messages.append({"type": "distractor", "text": pool[di]})
            di += 1

    add_distractors(inter_distractors)  # opening block
    for i, fact in enumerate(facts):
        for m in fact["messages"]:
            messages.append({"type": "fact", "text": m})
        if i < len(facts) - 1:
            add_distractors(inter_distractors)  # gaps between facts only
    add_distractors(tail_distractors)           # exact tail length

    # Forbidden-fact (hallucination) flagging was dropped, so recall questions
    # carry only the expected-fact keyword groups.
    recall_questions = [
        {
            "id": f["id"],
            "fact_type": f["type"],
            "question": f["question"],
            "expected_facts": f["expected"],
        }
        for f in FACTS  # keep original (unshuffled) order for stable reporting
    ]

    n_fact_msgs = sum(1 for m in messages if m["type"] == "fact")
    n_distractors = sum(1 for m in messages if m["type"] == "distractor")

    return {
        "id": "long_balanced_v3_300",
        "category": "long_conversation_balanced_large",
        "description": (
            f"{len(FACTS)} fact topics "
            f"({sum(1 for f in FACTS if f['type']=='third_party')} third-party, "
            f"{sum(1 for f in FACTS if f['type']=='user_self')} user-self), "
            f"{n_fact_msgs} fact messages + {n_distractors} distractors "
            f"= {len(messages)} build-up turns, then {len(recall_questions)} recall "
            "questions. Facts are placed before a heavy distractor tail so all are "
            "evicted from the 4000-token context cap by recall time."
        ),
        "messages": messages,
        "recall_questions": recall_questions,
    }


def main():
    scenario = build_scenario()
    out_path = Path(__file__).resolve().parent / "scenarios.json"
    out_path.write_text(json.dumps([scenario], indent=2, ensure_ascii=False), encoding="utf-8")

    n_fact = sum(1 for m in scenario["messages"] if m["type"] == "fact")
    n_dist = sum(1 for m in scenario["messages"] if m["type"] == "distractor")
    fact_positions = [i for i, m in enumerate(scenario["messages"]) if m["type"] == "fact"]
    print(f"Wrote {out_path}")
    print(f"  build-up turns: {len(scenario['messages'])} ({n_fact} fact, {n_dist} distractor)")
    print(f"  recall questions: {len(scenario['recall_questions'])}")
    print(f"  last fact at turn: {fact_positions[-1]} / {len(scenario['messages'])} "
          f"(tail of {len(scenario['messages']) - fact_positions[-1] - 1} distractors after)")


if __name__ == "__main__":
    main()
