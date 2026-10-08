"""
Async client wrapper for A2A agents with OTEL noise suppressed and run-scoped tracing helpers.
"""
import logging
import os
from dataclasses import dataclass
from typing import Any, List
from uuid import uuid4
# os.environ["OTEL_SDK_DISABLED"] = "true"
# os.environ["OTEL_TRACES_EXPORTER"] = "none"
# os.environ["OTEL_METRICS_EXPORTER"] = "none"
os.environ["A2A_TELEMETRY_DISABLED"] = "true"
import httpx
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types import Message, Role, TaskState, TextPart
from a2a.utils.artifact import get_artifact_text
from a2a.utils.parts import get_text_parts
from shared.config import PipelineConfig
from shared.logging_utils import configure_component_logger

logger = configure_component_logger(
    logger_name=__name__,
    component_group="gateway",
    component_name="gateway",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="gateway-a2a-client-console",
    file_tag="gateway-a2a-client-file",
    file_mode="a",
)


DEFAULT_AGENT_HTTP_TIMEOUT_SECONDS = PipelineConfig.GATEWAY_AGENT_HTTP_TIMEOUT_SECONDS

@dataclass
class AgentConfig:
    """
    Configuration class for the A2AClient.
    """
    name: str
    url: str

@dataclass
class SkillInfo:
    """
    Metadata returned by an A2A agent when listing skills.
    """
    agent_name: str
    skill_name: str
    skill_id: str
    agent_description: str
    skill_description: str
    skill_examples: list[str]
    tags: list[str]


class A2AClient:
    """
    Docstring for A2AClient
    """
    def __init__(self, config: AgentConfig):
        """
        Initialize the A2AClient with the given configuration.
        Args:
            config: Configuration for the A2A client.
        """
        self.config: AgentConfig = config
        self.client: Any | None = None
        self.card: Any | None = None
        self.httpx_client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        """
        Establish a connection to the A2A server using ClientFactory.
        """
        # Create httpx client with custom timeout
        timeout_seconds = DEFAULT_AGENT_HTTP_TIMEOUT_SECONDS
        self.httpx_client = httpx.AsyncClient(timeout=timeout_seconds)

        client_config = ClientConfig(httpx_client=self.httpx_client)

        self.client = await ClientFactory.connect(
            self.config.url,
            client_config=client_config
        )

    def _cleanup(self) -> None:
        """
        Cleanup resources associated with the A2A client.
        """
        self.client = None
        self.card = None
        self.httpx_client = None

    async def disconnect(self) -> None:
        """
        Disconnect from the A2A server and cleanup resources.
        """
        if self.client:
            await self.client.close()
        if hasattr(self, 'httpx_client') and self.httpx_client:
            await self.httpx_client.aclose()
        self._cleanup()
    
    async def get_skills(self) -> List[SkillInfo]:
        """
        Retrieve the agent card from the A2A server using the client's get_card method.
        """
        if not self.client:
            raise RuntimeError("Client not connected. Call connect() first.")
        self.card = await self.client.get_card()
        skills_info = [
            SkillInfo(
                agent_name=self.config.name,
                skill_name=skill.name,
                skill_id=skill.id,
                agent_description=self.card.description,
                skill_description=skill.description,
                skill_examples=skill.examples or [],
                tags=skill.tags or [],
            )
            for skill in self.card.skills
        ]
        return skills_info
    
    async def execute_skill(self, skill_name: str, input_data: str, *, run_id: str) -> str:
        """
        Execute a skill on the A2A server.
        
        Args:
            skill_name: Name of the skill to execute.
            input_data: Input data for the skill.
            run_id: Stable run identifier for tracing and logging.
        Returns:
            str: Output from the skill execution.
        """
        if not self.client:
            raise RuntimeError("Client not connected. Call connect() first.")
        logger.info("A2AClient.execute_skill skill=%s run_id=%s", skill_name, run_id)
        part = TextPart(text=input_data)
        message = Message(
            message_id=uuid4().hex,
            parts=[part],
            role=Role.user,
        )
        request_metadata = {"run_id": run_id} if run_id else None
        async for event in self.client.send_message(message, request_metadata=request_metadata):
            # event is either a Message or a ClientEvent (tuple of Task and update)
            if isinstance(event, Message) and hasattr(event, 'parts'):
                return get_text_parts(event.parts)
            else:
                task, _ = event
                if task.status.state == TaskState.completed:
                    if task.artifacts:
                        return get_artifact_text(task.artifacts[0])
        
        # If we exit the loop without returning, no valid response was found
        raise RuntimeError("No text response received from agent")
