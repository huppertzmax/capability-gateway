import logging
import uuid
from typing import Any, Dict, List, Optional
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from gateway.vectorstore.embeddings.base_embedding import BaseEmbedding
from pipelines.gateway.gateway.services.capability_schemas import Capability
from pipelines.gateway.vectorstore.embeddings.openai_embedding import OpenAIEmbedding
from pipelines.gateway.vectorstore.vectorstore_client import VectorStoreClient

logger = logging.getLogger(__name__)

class QdrantVectorStoreClient(VectorStoreClient):

    @staticmethod
    def _format_search_results(results: List[Dict[str, Any]], max_items: int = 5) -> str:
        """
        Format a compact search summary for logs.

        Avoid logging full payloads (can be very large and includes full descriptions/schemas).
        """
        items: List[str] = []
        for r in results[:max_items]:
            payload = r.get("payload") or {}
            capability_id = payload.get("capability_id")
            score = r.get("score")
            if capability_id is None:
                items.append(f"(unknown, score={score})")
            else:
                items.append(f"{capability_id} (score={score})")
        suffix = "" if len(results) <= max_items else f", …(+{len(results) - max_items})"
        return "[" + ", ".join(items) + suffix + "]"

    def __init__(
        self,
        storage_path: str = "./qdrant_data",
        collection_name: str = "capabilities",
        distance: str = "Cosine",
        vector_size: Optional[int] = None,
        embedding: BaseEmbedding = OpenAIEmbedding(),
    ):
        self.client = QdrantClient(path=storage_path)
        self.collection_name = collection_name
        self.distance = distance
        self.vector_size = vector_size
        self.embedding = embedding
        if vector_size is not None:
            self.ensure_collection(vector_size)
        else:
            try:
                _ = self.client.get_collection(self.collection_name)
            except Exception:
                logger.debug("Qdrant collection %s not found yet", self.collection_name)

    def ensure_collection(self, vector_size: int) -> None:
        params = qmodels.VectorParams(
            size=vector_size,
            distance=self.distance,
        )

        if self.client.collection_exists(self.collection_name):
            info = self.client.get_collection(self.collection_name)
            existing = info.config.params.vectors

            existing_size = existing.size
            existing_distance = existing.distance

            if existing_size != vector_size:
                raise ValueError(
                    f"Qdrant collection '{self.collection_name}' "
                    f"vector_size mismatch: {existing_size} != {vector_size}"
                )

            if str(existing_distance) != str(self.distance):
                raise ValueError(
                    f"Qdrant collection '{self.collection_name}' "
                    f"distance mismatch: {existing_distance} != {self.distance}"
                )

            self.vector_size = existing_size
            logger.debug("Qdrant collection %s already exists", self.collection_name)
            return

        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=params,
        )

        self.vector_size = vector_size
        logger.debug(
            "Created Qdrant collection '%s' (vector_size=%d, distance=%s)",
            self.collection_name,
            vector_size,
            self.distance,
        )

    def upsert_capabilities(
        self,
        capabilities: list[Capability],
        batch_size: int = 128,
    ) -> None:
        caps = list(capabilities)
        if not caps:
            logger.debug("No capabilities to upsert")
            return

        logger.debug(
            "Preparing to upsert %d capabilities into Qdrant collection '%s'",
            len(caps),
            self.collection_name,
        )

        summaries = [cap.summary for cap in caps]
        vectors = self.embedding.embed(summaries)

        if self.vector_size is None:
            self.ensure_collection(len(vectors[0]))

        points: List[qmodels.PointStruct] = []
        total_upserted: List[str] = []
        current_batch_caps: List[str] = []

        for cap, vec in zip(caps, vectors):
            payload = {
                "capability_id": cap.capability_id,
                "summary": cap.summary,
                "description": cap.description,
                "source_name": cap.source_name,
                "kind": cap.kind.value if hasattr(cap.kind, "value") else str(cap.kind),
                "capability_name": cap.capability_name,
                "input_type": cap.input_type,
                "input_schema": cap.input_schema,
                "output_type": cap.output_type,
                "output_schema": cap.output_schema,
            }

            pid = str(uuid.uuid5(uuid.NAMESPACE_OID, cap.capability_id))
            points.append(
                qmodels.PointStruct(
                    id=pid,
                    vector=vec,
                    payload=payload,
                )
            )

            current_batch_caps.append(f"{cap.capability_id} ({cap.capability_name})")

            if len(points) >= batch_size:
                logger.debug("Upserting batch of %d capabilities into '%s'", len(points), self.collection_name)
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=points,
                )
                total_upserted.extend(current_batch_caps)
                points = []
                current_batch_caps = []

        if points:
            logger.debug("Upserting final batch of %d capabilities into '%s'", len(points), self.collection_name)
            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
            )
            total_upserted.extend(current_batch_caps)

        logger.debug(
            "Finished upsert: total %d capabilities added to '%s'. Examples: %s",
            len(total_upserted),
            self.collection_name,
            (total_upserted[:10] if total_upserted else []),
        )

    def search(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:

        qvec = self.embedding.embed([query])[0]

        results = self.client.query_points(
            collection_name=self.collection_name,
            with_payload=True,
            query=qvec,
            limit=top_k,
        )

        result = [
            {
                "payload": r.payload,
                "score": r.score,
            }
            for r in results.points
        ]
        logger.debug(
            "Vector search query=%r top_k=%d returned=%d %s",
            query,
            top_k,
            len(result),
            self._format_search_results(result),
        )

        return result

    def delete_collection(self) -> None:
        try:
            self.client.delete_collection(self.collection_name)
        except Exception as e:
            logger.exception("Failed to delete collection: %s", e)
            raise

    def get_capability_by_id(self, capability_id: str) -> Capability:
        capability = None
        pid = str(uuid.uuid5(uuid.NAMESPACE_OID, capability_id))
        result = self.client.retrieve(
            collection_name=self.collection_name,
            ids=[pid],
            with_payload=True,
        )

        if result:
            point = result[0]
            capability = Capability(**point.payload)

        return capability

    def close(self) -> None:
        pass
