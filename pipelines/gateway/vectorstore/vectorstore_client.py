from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Iterable, List

EmbedFn = Callable[[str], List[float]]

class VectorStoreClient(ABC):

    @abstractmethod
    def ensure_collection(self, vector_size: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def upsert_capabilities(self, capabilities: Iterable[Any], embed_fn: EmbedFn, batch_size: int = 128) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str, embed_fn: EmbedFn, top_k: int = 3) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def delete_collection(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
