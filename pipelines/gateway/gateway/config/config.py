"""
Gateway configuration loader utilities.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from gateway.gateway.clients.a2a.a2a_client import AgentConfig
from gateway.gateway.clients.mcp.mcp_client import ServerConfig
from shared.config import PipelineConfig
from shared.logging_utils import configure_component_logger

logger = configure_component_logger(
    logger_name=__name__,
    component_group="gateway",
    component_name="gateway",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="gateway-config-console",
    file_tag="gateway-config-file",
    file_mode="a",
)

CALLER_AGENT_ID_HEADER = "X-Caller-Agent-Id"
RUN_ID_HEADER = "X-Run-Id"

@dataclass(frozen=True)
class CallerAclPolicy:
    """ACL entry for one caller."""
    allow_agents: List[str]
    allow_tools: List[str]

    def __str__(self) -> str:
        return f"-> Agents: {self.allow_agents}\n-> Tools: {self.allow_tools}\n"


@dataclass(frozen=True)
class AclPolicy:
    """ACL policy mapping from caller_agent_name to permissions."""

    policies_by_caller: Mapping[str, CallerAclPolicy]

    def get_policy(self, caller_agent_name: str) -> Optional[CallerAclPolicy]:
        """Return the policy for a caller, or None when the caller is unknown."""
        return self.policies_by_caller.get(caller_agent_name)


@dataclass
class GatewayConfig:
    """Structured representation of the gateway configuration."""
    mcp_servers: List[ServerConfig]
    agents: List[AgentConfig]
    acl_policy: AclPolicy


def _load_bloat_allow_tools_by_caller() -> Mapping[str, List[str]]:
    """
    Load bloat tool permissions from the bloat config file when bloat is enabled.
    Returns:
        Mapping[str, List[str]]: Mapping caller_id -> allowed bloat tool names.
    """
    if not PipelineConfig.ENABLE_MCP_BLOAT:
        return {}
    bloat_config_path = Path(__file__).resolve().parents[4] / "mcp-server" / "bloat" / "tool_descriptions" / "config.json"
    payload = json.loads(bloat_config_path.read_text(encoding="utf-8"))
    allow_tools_by_caller = payload.get("allow_tools_by_caller")
    return allow_tools_by_caller


def _merge_bloat_tools_into_acl(content: Dict[str, Any], allow_tools_by_caller: Mapping[str, List[str]]) -> None:
    """
    Merge bloat tools into the ACL JSON payload (in-place).
    Args:
        content: ACL JSON payload (caller_id -> policy object).
        allow_tools_by_caller: Mapping caller_id -> bloat tool allow list.
    Returns:
        None: Mutates content in place.
    """
    for caller_id, bloat_tools in allow_tools_by_caller.items():
        raw_policy = content.get(caller_id)
        existing_tools = raw_policy.get("allow_tools")
        merged = list(dict.fromkeys([*existing_tools, *bloat_tools]))
        raw_policy["allow_tools"] = merged


def load_gateway_config(acl_policy_config_path: Path) -> GatewayConfig:
    """
    Load gateway configuration from PipelineConfig (and ACL from JSON).
    Args:
        acl_policy_config_path: Path to the ACL policy configuration file.
    Returns:
        GatewayConfig: Parsed configuration.
    """
    mcp_servers = [
        ServerConfig(name="osv-server", url=f"http://localhost:{PipelineConfig.OSV_MCP_PORT}/mcp", transport_type="http"),
        ServerConfig(name="system-data-server", url=f"http://localhost:{PipelineConfig.SYSTEM_MCP_PORT}/mcp", transport_type="http"),
        ServerConfig(name="notification-server", url=f"http://localhost:{PipelineConfig.NOTIFICATION_MCP_PORT}/mcp", transport_type="http"),
    ]
    if PipelineConfig.ENABLE_MCP_BLOAT:
        mcp_servers.append(
            ServerConfig(
                name="bloat-server",
                url=f"http://localhost:{PipelineConfig.BLOAT_MCP_PORT}/mcp",
                transport_type="http",
            )
        )
    agents_config = [
        AgentConfig(name="osv_agent", url=PipelineConfig.get_agent_url(PipelineConfig.OSV_AGENT_PORT)),
        AgentConfig(name="matcher_agent", url=PipelineConfig.get_agent_url(PipelineConfig.MATCHER_AGENT_PORT)),
        AgentConfig(name="notification_agent", url=PipelineConfig.get_agent_url(PipelineConfig.NOTIFICATION_AGENT_PORT)),
    ]
    acl_policy = load_acl_policy(acl_policy_config_path, agents_config)
    return GatewayConfig(mcp_servers=mcp_servers, agents=agents_config, acl_policy=acl_policy)


def load_acl_policy(config_path: Path, agent_configs: List[AgentConfig]) -> AclPolicy:
    """
    Load and validate the ACL policy JSON file.
    Args:
        config_path: Path to the JSON file containing caller -> allow_agents/allow_tools.
        agent_configs: List of agent configurations used to validate allow_agents references.
    Returns:
        AclPolicy: Validated policy mapping.
    """
    resolved_path = Path(config_path)
    with resolved_path.open("r", encoding="utf-8") as handle:
        content: Any = json.load(handle)
    if PipelineConfig.ENABLE_MCP_BLOAT:
        allow_tools_by_caller = _load_bloat_allow_tools_by_caller()
        _merge_bloat_tools_into_acl(content, allow_tools_by_caller)
    policies: Dict[str, CallerAclPolicy] = {}
    for caller_agent_id, raw_policy in content.items():
        allow_agents = raw_policy.get("allow_agents", [])
        allow_tools = raw_policy.get("allow_tools", [])
        unknown_agents = sorted(
            agent_id
            for agent_id in allow_agents
            if agent_id not in {agent.name for agent in agent_configs}
        )
        if unknown_agents:
            raise ValueError(
                f"ACL policy for '{caller_agent_id}' references unknown agent_ids: {', '.join(unknown_agents)}"
            )
        policies[caller_agent_id] = CallerAclPolicy(
            allow_agents=allow_agents,
            allow_tools=allow_tools,
        )
    logger.info(f"Loaded ACL policy for {len(policies)} callers:")
    for caller, policy in policies.items():
        logger.info(f"Caller '{caller}' has access to:\n{policy}")
    return AclPolicy(policies_by_caller=policies)
