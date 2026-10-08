"""
HTTP client utilities for interacting with the gateway.
"""

from __future__ import annotations

import json
import logging
import httpx
from dataclasses import dataclass
from typing import Any, Dict, Optional
from shared.config import PipelineConfig


logger = logging.getLogger(__name__)


class GatewayRequestError(RuntimeError):
    """
    Raised when the gateway responds with an error or cannot be reached.
    """


@dataclass
class AsyncGatewayHttpClient:
    """
    Async wrapper around httpx to call gateway endpoints with shared behavior.
    """

    base_url: str
    timeout: float = PipelineConfig.GATEWAY_AGENT_HTTP_TIMEOUT_SECONDS
    default_headers: Optional[Dict[str, str]] = None

    async def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Perform an async GET request against the gateway.
        Args:
            path: Relative endpoint path (e.g. '/tools').
            params: Optional query parameters.
        Returns:
            str: JSON response as a serialized string.
        """
        return await self._request("GET", path, params=params, headers=headers)

    async def post(
        self,
        path: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Perform an async POST request against the gateway.
        Args:
            path: Relative endpoint path.
            payload: Request body.
        Returns:
            str: JSON response as a serialized string.
        """
        return await self._request("POST", path, json_payload=payload, headers=headers)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_payload: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> str:
        url = f"{self.base_url.rstrip('/')}{path}"
        merged_headers: Dict[str, str] = {}
        if self.default_headers:
            merged_headers.update(self.default_headers)
        if headers:
            merged_headers.update(headers)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json_payload,
                    headers=merged_headers or None,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()
                return json.dumps(data)
        except httpx.HTTPStatusError as exc:
            error_detail = self._extract_error_detail(exc.response)
            logger.error(
                f"Gateway returned error {exc.response.status_code}: {error_detail}",
                exc_info=True,
            )
            raise GatewayRequestError(
                f"Gateway error: {exc.response.status_code} - {error_detail}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error(f"Gateway request failed: {exc}", exc_info=True)
            raise GatewayRequestError("Gateway request failed") from exc
        except ValueError as exc:
            logger.error(f"Gateway returned invalid JSON: {exc}", exc_info=True)
            raise GatewayRequestError("Gateway returned invalid JSON") from exc

    def _extract_error_detail(self, response: httpx.Response) -> str:
        """
        Extract a concise error message from a gateway HTTP response.
        Args:
            response (httpx.Response): Gateway response with a non-success status code.
        Returns:
            str: Best-effort error description.
        """
        try:
            data = response.json()
        except ValueError:
            return response.text.strip() or response.reason_phrase
        if isinstance(data, dict):
            for key in ("detail", "message", "error"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return json.dumps(data)
        return str(data)
