"""
Central configuration for NLP Code pipelines.
Defines ports and service connection details to avoid hardcoded conflicts.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(env_path)

if not env_path.exists():
    raise FileNotFoundError(f"Missing .env file!")

class PipelineConfig:

    # Langfuse
    LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
    LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
    LANGFUSE_HOST = os.getenv("LANGFUSE_HOST")

    # Model
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL")
    OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    OPENAI_REQUEST_TIMEOUT_SECONDS = int(os.getenv("OPENAI_REQUEST_TIMEOUT_SECONDS"))
    OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES"))

    # Gateway Service
    GATEWAY_AGENT_HTTP_TIMEOUT_SECONDS = float(os.getenv("GATEWAY_AGENT_HTTP_TIMEOUT_SECONDS"))
    GATEWAY_PORT = int(os.getenv("GATEWAY_PORT"))
    GATEWAY_MCP_PORT = int(os.getenv("GATEWAY_MCP_PORT"))
    GATEWAY_HOST = os.getenv("GATEWAY_HOST")

    # Base URLs (Convenience)
    GATEWAY_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}"
    GATEWAY_MCP_URL = f"http://{GATEWAY_HOST}:{GATEWAY_MCP_PORT}/mcp"
    
    # Capability MCP Servers 
    OSV_MCP_PORT = int(os.getenv("OSV_MCP_PORT"))
    SYSTEM_MCP_PORT = int(os.getenv("SYSTEM_MCP_PORT"))
    NOTIFICATION_MCP_PORT = int(os.getenv("NOTIFICATION_MCP_PORT"))
    BLOAT_MCP_PORT = int(os.getenv("BLOAT_MCP_PORT"))
    
    # Agents
    OSV_AGENT_PORT = int(os.getenv("OSV_AGENT_PORT"))
    MATCHER_AGENT_PORT = int(os.getenv("MATCHER_AGENT_PORT"))
    NOTIFICATION_AGENT_PORT = int(os.getenv("NOTIFICATION_AGENT_PORT"))

    # Simple Pipeline timeouts
    SIMPLE_AGENT_RUN_TIMEOUT_SECONDS = int(os.getenv("SIMPLE_AGENT_RUN_TIMEOUT_SECONDS"))
    SIMPLE_A2A_TIMEOUT_SECONDS = int(os.getenv("SIMPLE_A2A_TIMEOUT_SECONDS"))

    # MCP Bloat
    ENABLE_MCP_BLOAT = os.getenv("ENABLE_MCP_BLOAT", "false").strip().lower() in {"1", "true", "yes"}
    MCP_BLOAT_TOOL_COUNT = int(os.getenv("MCP_BLOAT_TOOL_COUNT", "-1"))

    
    @classmethod
    def get_agent_url(cls, port: int) -> str:
        return f"http://localhost:{port}"
    
    @classmethod
    def get_mcp_url(cls, port: int) -> str:
        return f"http://localhost:{port}/mcp"
