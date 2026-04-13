from sentence_transformers import SentenceTransformer, util

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "gemma3:4b"

EMBED_MODEL = SentenceTransformer('all-MiniLM-L6-v2')