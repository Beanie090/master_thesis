import argparse, math, os
import numpy as np
import faiss
from typing import List, Tuple, Dict
from sentence_transformers import SentenceTransformer


memories = [
    "The user's favorite artist is Lana Del Rey.",
    "The user's companion is named Léa, they met at management school two years ago.",
    "Last summer the user and Léa went to see Lana del rey, they both loved it very much.",
    "The user lives in Bruxelles since a year.",
    "Léa is also a big fan of Lana Del Rey, she contributed to the user's love for her.",
    "Léa lives in Metz, she was born there and hasn't travelled very much yet",
    "The user loves riding the electric skateboard, he is very passionate about it.",
    "Grandmother cooks amazing dumplings, and always makes the perfect fish",
    "Grandfather is very good at fixing things, he is a handyman.",
    "The user played the battery in a brass band but stopped because he moved to Bruxelles a year ago.",
    "Noémie loves climbing, she's been doing it since middle school.",
    "The user's first car was a black Renault Clio, he liked it very much, espacially how agile and resilient it is. It finally gave up in 2023 after 15 years and 250,000 km.",
    "Julie started to learn the electric guitar a year ago, but she hasn't been very consistent and hasn't made much progress yet.",
    "Benjamin is a long time friend of the user, they met in middle school and have been close friends ever since. Even though they don't see each other very often anymore.",
    "Julie and the user met on the street on the summer of 2024, Julie was riding a regular longboard and the user was on his electric skateboard. They started to ride togheter very often that summer.",
    "Emma loves dancing, she danced a lot with the user on the cruise ship where they met in summer 2024.",
    "The user avoids eating oyster after a food poisoning incident during christmas family dinner in 2023.",
    "The user is allergic to pollen, however he doesn't take his prescribed medication. This is why he often has a runny nose in spring.",
    "Adrien and Louis are the user's best friends in belgium, they met during the ski trip of their engineering school in february 2024.",
    "Adrien and Louis are twin brothers, they are both passionate about gym and motorcycles. Which influenced the user to start working out with them, they would often go to the gym togheter and cook dinner after. Their go to dish is grilled chicken legs on the barbecue",
]


questions_answers = [
    ("Who's my favorite artist ?", 0),
    ("What is my companion name?", 1),
    ("Did Léa travel much?", 5),
    ("How did i meet Julie?", 14),
    ("Why do i love lana del rey?", 4),
    ("What did i like about my first car?", 11),
    ("Why did i start working out?", 19),
    ("What is my favorite activity?", 6),
    ("What did i do with Léa last summer?", 2),
    ("What does i do with Adrien and Louis?", 19),
    ("Who loves climbing?", 10),
    ("Why do i have a runny nose ?", 17),
    ("Is Julie good at playing the guitar?", 12),
    ("Who can cook dumplings and fish?", 7),
    ("Do i eat oysters?", 16),
    ("How long did my clio run for?", 11),
    ("Did i meet anyone at ski?", 18),
    ("Who can repair anything?", 8),
    ("What did i enjoy doing on the ship?", 15),
    ("Who is my best friend in belgium?", 18),
    ("where is my house?", 3),
    ("why did i stopped music?", 9),
]


class Embedder:
    def __init__(self, model_name, is_e5):

        self.model = SentenceTransformer(model_name)
        self.is_e5 = is_e5

    def _prefix(self, texts, is_query):
        if not self.is_e5:
            return texts
        prefix = "query: " if is_query else "passage: "
        return [prefix + t for t in texts]

    def encode_memories(self, memories):
        prefixed = self._prefix(memories, is_query=False)
        return self.model.encode(prefixed, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)

    def encode_queries(self, queries):
        prefixed = self._prefix(queries, is_query=True)
        return self.model.encode(prefixed, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)

def build_faiss(emb_memories):
    d = emb_memories.shape[1]
    index = faiss.IndexFlatIP(d)
    index.add(emb_memories)
    return index

def recall_at_k(ranked, target_id, k):
    if target_id in ranked[:k]:
        return 1.0 
    else:
        return 0.0

def mrr_at_k(ranked, target_id, k):
    for i, idx in enumerate(ranked[:k], start=1):
        if idx == target_id:
            return 1.0 / i
    return 0.0

def ndcg_at_k(ranked, target_id, k):
    for i, idx in enumerate(ranked[:k], start=1):
        if idx == target_id:
            return 1.0 / math.log2(i + 1)
    return 0.0

def evaluate(index, emb_queries, target_ids, top_k=5):
    
    D, I = index.search(emb_queries, top_k)

    scores = []
    for i in range(len(target_ids)):
        score = recall_at_k(list(I[i]), target_ids[i], top_k)
        scores.append(score)
    r = np.mean(scores)
    
    scores = []
    for i in range(len(target_ids)):
        score = mrr_at_k(list(I[i]), target_ids[i], top_k)
        scores.append(score)
    m = np.mean(scores)

    scores = []
    for i in range(len(target_ids)):
        score = ndcg_at_k(list(I[i]),target_ids[i], top_k)
        scores.append(score)
    n = np.mean(scores)

    return {"Recall@{}".format(top_k): r, "MRR@{}".format(top_k): m, "nDCG@{}".format(top_k): n}, D, I


def print_rankings(label, queries, memories, I, D, show_k: int = 3):
    print(f"\n== Top-{show_k} rankings for {label} ==")
    for qi, q in enumerate(queries):
        print(f"\nQ{qi+1}: {q}")
        for j in range(min(show_k, I.shape[1])):
            di = int(I[qi, j])
            score = float(D[qi, j])
            snippet = memories[di][:160].replace("\n", " ")
            print(f"  {j+1}. score={score:.4f}  doc#{di}: {snippet}")


def test_model(model_name, is_e5, memories, questions_answers, top_k=5):
    emb = Embedder(model_name, is_e5=is_e5)
    doc_vecs = emb.encode_memories(memories)
    queries = [q for q, _ in questions_answers]
    target_ids   = [g for _, g in questions_answers]
    q_vecs  = emb.encode_queries(queries)
    idx     = build_faiss(doc_vecs)
    metrics, D, I = evaluate(idx, q_vecs, target_ids, top_k=top_k)
    return metrics, D, I, queries

def main():

    model_a   = "sentence-transformers/all-MiniLM-L6-v2"
    is_e5_a   = False
    model_b   = "intfloat/multilingual-e5-base"
    is_e5_b   = True
    top_k     = 5
    show_k    = 3

    mA, DA, IA, queriesA = test_model(model_a, is_e5_a, memories, questions_answers, top_k=top_k)
    mB, DB, IB, queriesB = test_model(model_b, is_e5_b, memories, questions_answers, top_k=top_k)

    print_rankings("A", queriesA, memories, IA, DA, show_k=show_k)
    print_rankings("B", queriesB, memories, IB, DB, show_k=show_k)


    print(f"memories: {len(memories)}  queries: {len(questions_answers)}  top_k={top_k}")


    print("\nA:", model_a, mA)
    print("B:", model_b, mB)



if __name__ == "__main__":
    main()
