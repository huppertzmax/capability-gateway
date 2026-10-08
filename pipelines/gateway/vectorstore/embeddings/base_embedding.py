from abc import ABC, abstractmethod
from typing import List


class BaseEmbedding(ABC):

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        pass

    def __call__(self, text: str) -> List[float]:
        return self.embed([text])[0]
