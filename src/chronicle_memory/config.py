from dataclasses import dataclass, field
from pathlib import Path
import os

from dotenv import load_dotenv


@dataclass
class Settings:
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = field(default="", repr=False)
    neo4j_database: str = "neo4j"
    namespace: str = "default"
    timezone: str = "Asia/Shanghai"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = field(default="", repr=False)
    model: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = field(default="", repr=False)
    tick_seconds: float = 30.0

    @classmethod
    def from_env(cls, env_file: Path | None = Path(".env")):
        # Only load the project's explicit .env, never search parent directories.
        if env_file is not None:
            load_dotenv(env_file, override=False)
        return cls(
            neo4j_uri=os.getenv("MEMORY_NEO4J_URI", "bolt://127.0.0.1:7687"),
            neo4j_user=os.getenv("MEMORY_NEO4J_USER", "neo4j"),
            neo4j_password=os.getenv("MEMORY_NEO4J_PASSWORD", ""),
            neo4j_database=os.getenv("MEMORY_NEO4J_DATABASE", "neo4j"),
            namespace=os.getenv("MEMORY_NAMESPACE", "default"),
            timezone=os.getenv("MEMORY_TIMEZONE", "Asia/Shanghai"),
            base_url=os.getenv("MEMORY_LLM_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.getenv("MEMORY_LLM_API_KEY", ""),
            model=os.getenv("MEMORY_LLM_MODEL", ""),
            embedding_model=os.getenv("MEMORY_EMBEDDING_MODEL", ""),
            embedding_base_url=os.getenv("MEMORY_EMBEDDING_BASE_URL", ""),
            embedding_api_key=os.getenv("MEMORY_EMBEDDING_API_KEY", ""),
            tick_seconds=float(os.getenv("MEMORY_TICK_SECONDS", "30")),
        )
