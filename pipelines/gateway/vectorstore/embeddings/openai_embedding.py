"""
OpenAI embedding adapter with strict run-id-bound Langfuse tracing.
"""
from typing import List, Optional
from langfuse.openai import OpenAI
from pipelines.gateway.vectorstore.embeddings.base_embedding import BaseEmbedding
from shared.config import PipelineConfig
from shared.model_loader import LANGFUSE_MANAGER, resolve_run_id
from shared.run_context import require_run_id

class OpenAIEmbedding(BaseEmbedding):
    """Embedding client that requires a valid run_id for trace emission."""

    def __init__(
        self,
        api_key: str = PipelineConfig.OPENAI_API_KEY or "",
        base_url: str = PipelineConfig.OPENAI_API_BASE or "",
        model: str = PipelineConfig.OPENAI_EMBEDDING_MODEL,
        run_id: Optional[str] = None,
    ):
        """Initialize the OpenAI embedding client."""
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self.model = model
        self.run_id = run_id

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed texts and attach Langfuse trace id for the active run."""
        trace_id = self._get_trace_id()
        response = self.client.embeddings.create(
            model=self.model,
            input=texts,
            trace_id=trace_id
        )
        return [item.embedding for item in response.data]

    def _get_trace_id(self) -> str:
        """Resolve and validate the active run trace id for embeddings."""
        resolved_run_id = require_run_id(
            resolve_run_id(self.run_id),
            source="gateway embedding context",
        )
        trace_payload = LANGFUSE_MANAGER.ensure_trace(
            resolved_run_id,
            name="embeddings",
            metadata={"component": "gateway_embeddings"},
        )
        if trace_payload is None:
            raise RuntimeError(f"Failed to initialize Langfuse trace for embeddings.")
        trace_id = trace_payload["trace_id"]
        return trace_id
