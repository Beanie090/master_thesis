# Multi-Session Long-Term Memory Stress Test

*Chapter summary.* This chapter evaluates whether LEAi's persistent memory architecture provides
recall beyond the reach of the language model's context window. We argue that the earlier
short-conversation comparison was confounded by context-window capacity, and replace it with a
long-conversation, cost-bounded stress test in which thirty facts are embedded in a 277-turn dialogue
and recalled only after every fact has been evicted from the in-context window. By toggling retrieval
on and off under otherwise identical conditions, we isolate the contribution of retrieval-augmented
memory. The results show that retrieval accounts for almost all recall of facts about third parties,
raising factual accuracy from 0.25 to 0.98 on that category and eliminating the confident
hallucinations the no-retrieval system otherwise produces, while facts about the user are handled by
the always-in-context profile independently of retrieval.

## 1. Motivation and Goal

The initial evaluation of LEAi compared the system against a plain Gemini baseline over short
conversations of roughly thirty turns. This design proved insufficient as a test of long-term
memory: at that length the entire dialogue fits comfortably within the model's context window, so
any fact introduced earlier remains directly visible when a recall question is asked. Under those
conditions a model can answer correctly without any dedicated memory mechanism at all, simply by
attending to its context. The comparison therefore measured the capacity of the underlying language
model's context window rather than the contribution of LEAi's memory architecture.

To address this, we designed a long-conversation, cost-bounded stress test. Its goal is to evaluate
whether LEAi's persistent memory components — the distilled user profile and the
retrieval-augmented memory store (RAG) — actually contribute recall *beyond* what the context window
provides, and to isolate the specific contribution of retrieval. The test deliberately recreates the
realistic deployment regime in which a companion chatbot cannot afford to resend the entire
conversation history to the model on every turn, both for cost and latency reasons, and must instead
rely on persisted memory once older turns fall out of the in-context window.

## 2. Methodology

**Conversation design.** A single simulated conversation of 277 turns was generated. Thirty distinct
facts were embedded in it, each introduced over three consecutive messages (an introduction followed
by two detail messages), yielding 90 fact-bearing messages interleaved with 187 topically irrelevant
"distractor" messages. The facts were divided into two categories:

- **User-self facts (6):** information about the user themselves (birthday, food preferences, gym
  schedule, occupation, language being learned, thesis topic).
- **Third-party facts (24):** information about external entities — friends, family members, a pet,
  films, books, restaurants, a band, a podcast, and so on.

This taxonomy is central to the experiment because LEAi routes the two categories to different
memory stores (Section 3): user-self facts are consolidated into a continuously maintained profile,
whereas third-party facts are written to the retrieval store.

**Eviction guarantee.** All facts were placed within the first 210 turns, followed by a tail of 67
consecutive distractor turns. Combined with the context cap described below, this guarantees that by
the time any recall question is asked, every fact has been pushed out of the model's in-context
window. Recall therefore cannot be satisfied from visible conversation history; it must come from a
persistent memory mechanism, if at all.

**Context cap.** To emulate a cost-bounded deployment, the conversation history made available to the
model on each turn was capped at 3000 tokens (approximately 18–25 turns). The cap applies only to the
conversational transcript; the distilled profile and any retrieved memories are supplied through the
system instruction and are not subject to it.

**Two conditions (controlled comparison).** After the conversation was played out, each of the 30
recall questions was posed twice, from an identical post-conversation state:

- **No-retrieval condition:** the retrieval mechanism is disabled, so the model answers using only
  its always-in-context distilled profile.
- **With-retrieval condition:** the retrieval mechanism is enabled, giving the model access to the
  full memory stack (profile plus semantically retrieved facts).

Holding everything else constant and toggling only retrieval isolates the marginal contribution of
RAG. To ensure the two conditions began from exactly the same state, memory writes were disabled
during the recall phase and the conversation history was reset to its post-build-up checkpoint
between every question, preventing one condition's answer from leaking into the other's context.

**Grading.** Answers were graded by two methods. An automated grader checked each answer against a
set of expected keyword groups, awarding full credit when all groups were matched, partial credit
for some, and none otherwise. Because keyword matching cannot distinguish a confident fabrication
from a genuine recall — and because the entity name often appears in the question itself — every
answer was additionally graded manually for factual correctness against the planted facts.

## 3. Technical Implementation

LEAi is implemented as a Flask application backed by Google's `gemini-2.5-flash-lite` model for all
generation and a `sentence-transformers/all-MiniLM-L6-v2` encoder (384-dimensional embeddings) for
semantic memory. On every user message the system executes a deterministic memory pipeline:

1. **Scoring.** The message is rated for memory-worthiness on a 1–10 scale, conditioned on the
   previous six turns; only messages scoring at or above a threshold of 4 proceed.
2. **Extraction.** A self-contained fact is extracted from the message and its local context.
3. **Classification and routing.** The fact is classified as being about the companion, about the
   user, or about neither. Facts about the user are merged into a continuously rewritten
   **user-information profile**; facts about the companion update its persona description; all
   remaining facts (the third-party/external case) are embedded and stored in a **FAISS vector
   index** for retrieval.

At response time the prompt is assembled from the persona description, the current user profile, any
retrieved memories, and the token-capped recent transcript. Retrieval performs cosine-similarity
search over the FAISS index, returning up to five memories above a similarity threshold of 0.35;
this threshold was tuned to the embedder's characteristic similarity range, in which semantically
related question–fact pairs typically score between 0.3 and 0.5. Near-duplicate memories are merged
above a similarity of 0.85.

A key architectural property exploited by the experiment is that the user profile is injected into
the system instruction on every turn and is therefore never affected by the context cap, whereas
third-party facts live only in the retrieval store and are surfaced only when retrieval is enabled.
This is precisely what allows the two conditions to separate the two memory pathways.

The evaluation harness drives the application in-process through a test client, exercising the real
`/start`, `/chat`, and `/load_session` endpoints and the genuine memory pipeline. The scenario is
produced by a seeded generator for reproducibility, retrieval is toggled at runtime between
conditions, and results are written incrementally to survive interruptions. To prevent a dropped
connection from stalling the long run, each model call was given a 60-second timeout with automatic
retry.

## 4. Results

### 4.1 Automated grading

Over the 30 recall questions, average recall was 0.57 without retrieval and 0.93 with retrieval, a
marginal gain of +0.37. Broken down by fact type, the effect is concentrated exactly where the
architecture predicts:

| Fact type | No retrieval | With retrieval | Delta |
|---|---:|---:|---:|
| Third-party (24) | 0.48 | 0.96 | +0.48 |
| User-self (6) | 0.92 | 0.83 | -0.08 |

User-self facts are recalled well in both conditions, confirming that the always-in-context profile
handles them and retrieval adds nothing (indeed it slightly distracts). Third-party facts, which the
profile does not retain, are where retrieval roughly doubles recall.

### 4.2 Manual verification

Manual factual grading revealed that the automated grader substantially over-credited the
no-retrieval condition, for two reasons: the entity name frequently appears in the question (so an
answer echoing "Marc" scored a partial match even when it admitted ignorance), and keyword matching
cannot detect a confident but false answer. Correcting for this:

| Fact type | No retrieval | With retrieval | Delta |
|---|---:|---:|---:|
| Third-party (24) | 0.25 | 0.98 | +0.73 |
| User-self (6) | 0.96 | 0.88 | -0.08 |
| **Overall (30)** | **0.39** | **0.96** | **+0.57** |

Under factual grading, genuine third-party recall without retrieval falls to 0.25, and inspection
shows that almost all of this residual recall comes from a small number of third-party facts that
the classifier had stored in the user profile because they were phrased as user preferences (e.g., a
favourite restaurant, a recommended book), together with one guessable answer. With retrieval
enabled, third-party recall rises to 0.98 and the answers are factually correct rather than merely
keyword-matching.

### 4.3 Qualitative finding: confident hallucination

The most important qualitative result concerns *how* the system fails without retrieval. Of the 24
third-party questions, the no-retrieval condition produced an honest refusal eight times, but
produced a confident hallucination eight times — stating a plausible but entirely fabricated answer.
For example, it named the recommended film as *The Last of Us* rather than *The Grand Budapest
Hotel*, described a friend's eco-friendly-packaging startup as a "sustainable fashion" company, and
relocated the user's architect brother from Berlin to Lyon as a graphic designer. Enabling retrieval
eliminated these confabulations in every case. This is a qualitative, safety-relevant benefit that
the aggregate scores alone do not convey: retrieval does not merely improve recall, it replaces
fluent fabrication with grounded, correct answers.

The no-retrieval outcomes on the 24 third-party questions break down as follows:

| Outcome (no retrieval) | Count / 24 |
|---|---:|
| Correct (fact had leaked into profile) | 3 |
| Correct (lucky guess) | 1 |
| Partial (entity named, weak attribute) | 4 |
| Honest refusal ("I don't know") | 8 |
| Confident hallucination | 8 |

### 4.4 Per-question results

Grades are the manual factual scores (0.0 / 0.5 / 0.75 / 1.0). Answers are condensed to their factual
gist.

**User-self facts (6):**

| ID | Question | No-retrieval answer | Score | With-retrieval answer | Score |
|---|---|---|---:|---|---:|
| birthday | When is my birthday? | "April 25th" | 1.0 | "April 25th" | 1.0 |
| food | What food do I dislike / love? | "dislike mushrooms; love spicy ramen" | 1.0 | "dislike mushrooms; love spicy ramen" | 1.0 |
| gym | What days / time do I go to the gym? | "Mon/Wed/Fri" (time omitted) | 0.75 | "Mon/Wed/Fri" (time omitted) | 0.75 |
| thesis | What is my thesis about? | "AI companion chatbots, adapt/evolve, long-term memory" | 1.0 | self-referential, vaguer | 0.5 |
| language | Which language, and when do I study? | "Japanese, every morning" | 1.0 | "Japanese, every morning, half hour" | 1.0 |
| job | What do I do for work? | "remote backend software developer, Python" | 1.0 | "remote backend software developer, Python" | 1.0 |

**Third-party facts (24):**

| ID | Question | No-retrieval answer | Score | Outcome (no retrieval) | With-retrieval answer | Score |
|---|---|---|---:|---|---|---:|
| noemie | About friend Noemie? | "vibrant energy, cafes in Paris" | 0.0 | hallucination | "28, artist, Lyon" | 1.0 |
| grandmother | About grandmother Marie? | "sense of humour, roses/garden" | 0.5 | partial | "school teacher 30 yrs, rose garden" | 0.75 |
| anna | Place Anna dislikes, and why? | "(guessing) crowded/noisy" | 1.0 | correct (guess) | "crowded places, overwhelming, noise" | 1.0 |
| marc | Marc's running habits? | "as an AI I would not know" | 0.0 | refusal | "weekend runs, river path, 10 km" | 1.0 |
| film | Which film, known for what? | "The Last of Us" | 0.0 | hallucination | "Grand Budapest Hotel, Wes Anderson, pastel/symmetrical" | 1.0 |
| music | Which artist, her sound? | "Lana Del Rey, cinematic, dreamy, nostalgic" | 1.0 | correct (profile leak) | "Lana Del Rey, dreamy, melancholic, hazy" | 1.0 |
| brother | Thomas — job, where? | "graphic designer, in Lyon" | 0.0 | hallucination | "architect, Berlin, 5 years" | 1.0 |
| book | Which book, about what? | "Midnight Library, Matt Haig, different lives" | 1.0 | correct (profile leak) | "Midnight Library, Matt Haig, different lives" | 1.0 |
| coworker | David's diet? | "avoids mushrooms" | 0.0 | hallucination | "strict vegetarian, no meat" | 0.75 |
| uncle | Pierre — job, where? | "a mystery, you have not mentioned him" | 0.0 | refusal | "vineyard, Bordeaux, red wine" | 1.0 |
| neighbor | Mrs. Chen, her family? | "as an AI I do not know your neighbours" | 0.0 | refusal | "piano teacher, twin daughters ~10" | 1.0 |
| restaurant | Which restaurant, best dish? | "Le Petit Jardin, French onion soup" | 1.0 | correct (profile leak) | "Le Petit Jardin, French onion soup, near river" | 1.0 |
| sofia | Sofia — studying, goal? | "I do not have info about your friends" | 0.0 | refusal | "medicine, Barcelona, paediatrician" | 1.0 |
| dog | About the dog Biscuit? | "golden retriever" (swimming omitted) | 0.5 | partial | "golden retriever, loves swimming in lake" | 1.0 |
| band | Which band, from where? | "you have not told me their name" | 0.0 | refusal | "Phoenix, French indie pop, Versailles" | 1.0 |
| cousin | Lucas — job? | "I do not have memories of Lucas" | 0.0 | refusal | "photographer, travels, wildlife" | 1.0 |
| cafe | Which cafe, special? | "Brew & Bean" (midnight omitted) | 0.5 | partial | "Brew & Bean, open until midnight" | 1.0 |
| teacher | Mr. Dubois, interests? | "history" (chess omitted; odd framing) | 0.5 | partial | "history teacher, retired, chess" | 1.0 |
| game | Which game, known for what? | "The Last of Us" | 0.0 | hallucination | "Hollow Knight, hand-drawn art, metroidvania" | 1.0 |
| sister | Camille — job, where? | "landscape architect, near mountains" | 0.0 | hallucination | "veterinarian, animal clinic, Montreal" | 1.0 |
| colleague_trip | Yuki — where, what? | "Kyoto, bamboo grove" | 0.0 | hallucination | "Patagonia, trekking/camping, glaciers" | 1.0 |
| plant | Frank — what plant, care? | "I do not think I mentioned a plant Frank" | 0.0 | refusal | "fiddle-leaf fig, bright indirect light" | 1.0 |
| podcast | Which podcast, host? | "you have not mentioned a podcast" | 0.0 | refusal | "Deep Time, geology/Earth, Dr. Reed" | 1.0 |
| startup | Elena's company, where? | "sustainable fashion" | 0.0 | hallucination | "eco-friendly packaging, Amsterdam" | 1.0 |

## 5. Discussion

The results support the central argument of this evaluation. Under a realistic, cost-bounded context
budget, a large context window alone is insufficient for long-term conversational continuity: once
older turns are evicted, a model with no persistent memory either declines to answer or fabricates.
LEAi's two memory mechanisms address this in a complementary way. The distilled profile reliably
preserves facts about the user and remains available at every turn regardless of context length,
which is why retrieval offers no benefit for that category. Retrieval is the decisive mechanism for
the much larger and more open-ended class of facts about third parties and external entities, raising
factual recall from approximately 0.25 to 0.98 and, equally importantly, suppressing confident
hallucination. The two pathways are thus shown to be complementary rather than redundant.

## 6. Limitations

Several limitations should be noted. First, the evaluation rests on a single generated conversation
graded as a single run; given the non-determinism of the language model, repeated runs and averaging
would yield more robust point estimates. Second, the automated grader is keyword-based and lenient,
which is why manual factual grading was necessary; the manual grades, while more faithful, are
inevitably subjective at the margins. Third, the conversation content is synthetic, and the
distractor turns, though varied, are simpler than naturally occurring dialogue. Fourth, the
experiment surfaced a classifier limitation: a handful of third-party facts framed as user
preferences were misrouted into the user profile, which both inflates the no-retrieval baseline and
indicates room to refine the user/third-party classification. Finally, all generation used the
cost-optimised `flash-lite` model tier; a larger model might exhibit fewer hallucinations in the
no-retrieval condition, though the structural argument — that evicted facts are unrecoverable without
persistent memory — would remain unchanged.
