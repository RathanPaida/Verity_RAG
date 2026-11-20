"""Create collections, regular/TTL indexes, Atlas Vector Search + Atlas Search indexes.

    python -m scripts.setup_db
"""
from verity.config import settings
from verity.db import ensure_indexes

if __name__ == "__main__":
    print(f"db={settings.mongodb_db} embedding={settings.embedding_provider} dim={settings.embedding_dim}")
    ensure_indexes(settings.embedding_dim)
