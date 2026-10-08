"""
CVE data management agent that fetches OSV CVE data, summarizes it, and collaborates with the SBOM matcher.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable
from crewai import Crew, CrewOutput, LLM
from crewai.a2a import A2AConfig
from dotenv import load_dotenv
from shared.config import PipelineConfig
from shared.model_loader import build_crewai_llm
from simple.tools.architecture import load_arch_config, resolve_architecture
from simple.tools.crew_factory import build_crew_from_config


load_dotenv()

logger = logging.getLogger(__name__)

class OSVAgent:
    """Agent that manages CVE data."""
    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']

    def __init__(self, model_name: str | None = None, architecture: str | None = None) -> None:
        """
        Initialize the CVE data management agent with model, A2A configuration, and tasks.
        """
        self.model: LLM = build_crewai_llm(model_name)
        self.architecture: str = resolve_architecture(architecture)
        self.step_callback: Callable[[Any], None] | None = None
        self.a2a_config: list[A2AConfig] = [
            A2AConfig(
                endpoint=PipelineConfig.get_agent_url(PipelineConfig.MATCHER_AGENT_PORT) + '/.well-known/agent-card.json',
                timeout=PipelineConfig.SIMPLE_A2A_TIMEOUT_SECONDS,
                max_turns=1,
                trust_remote_completion_status=True
            )
        ]
        self.mcp_config: list[str] = [PipelineConfig.get_mcp_url(PipelineConfig.OSV_MCP_PORT)]
        self.run_id: str | None = None
        self.crew: Crew = self._build_crew()

    def _build_crew(self) -> Crew:
        """
        Build a fresh Crew instance to avoid cross-run memory retention.
        """
        agent_dir = Path(__file__).resolve().parent
        arch_config = load_arch_config(agent_dir, self.architecture)
        return build_crew_from_config(
            config=arch_config,
            model=self.model,
            mcp_config=self.mcp_config,
            run_id=self.run_id,
            a2a_config=self.a2a_config,
            step_callback=self.step_callback,
        )

    def set_step_callback(self, callback: Callable[[Any], None] | None) -> None:
        """Bind a Crew step callback used for unified MCP-style logging."""
        self.step_callback = callback

    def set_run_id(self, run_id: str | None) -> None:
        """Bind the active run id used for MCP request headers."""
        self.run_id = run_id

    async def invoke(self, query: str) -> CrewOutput:
        if self.run_id is None:
            raise ValueError("Run ID must be set before invoking OSVAgent.")
        self.crew = self._build_crew()
        inputs = {'query': query}
        result: CrewOutput = await self.crew.kickoff_async(inputs)
        return result
