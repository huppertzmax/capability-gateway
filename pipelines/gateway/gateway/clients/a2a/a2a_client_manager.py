"""
Aggregated A2A client manager coordinating multiple agent connections.
"""

import logging
from typing import Any, Dict, List, Optional
from gateway.gateway.clients.a2a.a2a_client import A2AClient, AgentConfig
from gateway.gateway.clients.a2a.a2a_client import SkillInfo
from shared.logging_utils import configure_component_logger

logger = configure_component_logger(
    logger_name=__name__,
    component_group="gateway",
    component_name="gateway",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="gateway-a2a-manager-console",
    file_tag="gateway-a2a-manager-file",
    file_mode="a",
)


class A2AClientManager:
    """
    Manage connections to multiple A2A agents.
    """

    def __init__(self, agent_configs: List[AgentConfig]):
        """
        Store agent configurations for subsequent connections.
        Args:
            agent_configs: Collection of A2A agent descriptors.
        """
        self.agent_configs = agent_configs
        self.clients: Dict[str, A2AClient] = {}

    async def connect_all(self) -> None:
        """
        Connect to each configured A2A agent sequentially.
        """
        logger.info("Starting connections to %d agents", len(self.agent_configs))
        for config in self.agent_configs:
            try:
                client = A2AClient(config)
                await client.connect()
                self.clients[config.name] = client
                logger.info("[%s] Connected to agent at %s", config.name, config.url)
            except Exception as exc:
                logger.error("[%s] failed to connect: %s", config.name, exc, exc_info=True)
        logger.info("Connected to %d out of %d agents", len(self.clients), len(self.agent_configs))

    async def disconnect_all(self) -> None:
        """
        Disconnect from all known A2A agents.
        """
        logger.info("Disconnecting from all agents")
        for client in self.clients.values():
            await client.disconnect()
        self.clients.clear()
        logger.info("Disconnected from all agents")

    async def get_agent_skills(self, agent_name: str) -> Optional[List[SkillInfo]]:
        """
        Retrieve the agent skills from a specific agent.
        Args:
            agent_name: Agent identifier.
        Returns:
            Optional[List[SkillInfo]]: List of skills if found and retrieved successfully.
        """
        if agent_name not in self.clients:
            logger.warning("Agent '%s' not connected", agent_name)
            return None
        try:
            skills = await self.clients[agent_name].get_skills()
            logger.info("[%s] Retrieved agent skills", agent_name)
            return skills
        except Exception as exc:
            logger.error("[%s] Failed to retrieve skills: %s", agent_name, exc, exc_info=True)
            return None

    async def get_all_agent_skills(self) -> Dict[str, List[SkillInfo]]:
        """
        Retrieve agent skills from all connected agents.
        Returns:
            Dict[str, List[SkillInfo]]: Mapping of agent name to list of skills.
        """
        all_skills: Dict[str, List[SkillInfo]] = {}
        for agent_name in self.clients.keys():
            try:
                skills = await self.clients[agent_name].get_skills()
                all_skills[agent_name] = skills
            except Exception as exc:
                logger.error("[%s] Failed to get skills: %s", agent_name, exc, exc_info=True)
        return all_skills

    async def execute_agent_skill(self, agent_name: str, agent_skill: str, arguments: Dict[str, Any], run_id: str | None = None) -> Any:
        """
        Execute a skill on a specific agent.
        Args:
            agent_name: Agent identifier.
            agent_skill: Skill to execute.
            arguments: Arguments for the skill.
            run_id: Optional run identifier propagated to the agent.
        Returns:
            Any: The result of the skill execution.
        """
        if agent_name not in self.clients:
            raise ValueError(f"Agent '{agent_name}' not connected")
        logger.info("A2AClientManager.execute_agent_skill agent=%s skill=%s run_id=%s", agent_name, agent_skill, run_id)
        # TODO potentially more advanced argument handling/conversion here if needed
        return await self.clients[agent_name].execute_skill(agent_skill, str(arguments), run_id=run_id)

async def initialize_a2a_manager(agent_configs: List[AgentConfig]) -> A2AClientManager:
    """
    Convenience helper that constructs and connects an A2AClientManager.
    Args:
        agent_configs: List of agent configuration descriptors.
    Returns:
        A2AClientManager: Ready-to-use client manager instance.
    """
    manager = A2AClientManager(agent_configs)
    await manager.connect_all()
    return manager
