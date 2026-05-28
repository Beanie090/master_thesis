import faiss
from sentence_transformers import SentenceTransformer


class Embedder:
    def __init__(self, model_name: str):
        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()

    def encode(self, text):
        return self.model.encode(
            text,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

    def faiss_index(self):
        return faiss.IndexFlatIP(self.dim)

    def similarity_search(self, query, index, k: int = 3):
        query_embedding = self.encode(query)
        D, I = index.search(query_embedding.reshape(1, -1), k)
        return D[0], I[0]

    def dimension(self) -> int:
        return self.dim
