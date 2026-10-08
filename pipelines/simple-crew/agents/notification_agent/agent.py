"""
Notification crew split into a tool agent (Markdown email + tool call) and a coordination agent.
"""
import logging
from pathlib import Path
from typing import Any, Callable, Optional
from crewai import Crew, CrewOutput, LLM
from shared.config import PipelineConfig
from shared.model_loader import build_crewai_llm
from simple.tools.architecture import load_arch_config, resolve_architecture
from simple.tools.crew_factory import build_crew_from_config

logger = logging.getLogger(__name__)


class NotificationAgent:
    """Agent that notifies owners of affected systems"""
    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']

    def __init__(self, model_name: Optional[str] = None, architecture: str | None = None) -> None:
        self.model: LLM = build_crewai_llm(model_name)
        self.architecture: str = resolve_architecture(architecture)
        self.step_callback: Callable[[Any], None] | None = None
        self.mcp_config: list[str] = [PipelineConfig.get_mcp_url(PipelineConfig.NOTIFICATION_MCP_PORT)]
        self.run_id: str | None = None
        self.crew: Crew = self._build_crew()

    def _build_crew(self) -> Crew:
        """
        Build a fresh Crew instance so notifications stay stateless across runs.
        """
        agent_dir = Path(__file__).resolve().parent
        arch_config = load_arch_config(agent_dir, self.architecture)
        return build_crew_from_config(
            config=arch_config,
            model=self.model,
            mcp_config=self.mcp_config,
            run_id=self.run_id,
            a2a_config=None,
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
            raise ValueError("Run ID must be set before invoking NotificationAgent.")
        self.crew = self._build_crew()
        inputs = {'query': query}
        result: CrewOutput = await self.crew.kickoff_async(inputs)
        return result
