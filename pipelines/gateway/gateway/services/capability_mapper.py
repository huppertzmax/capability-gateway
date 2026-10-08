from gateway.gateway.services.capability_schemas import Capability, CapabilitySummary, CapabilityDetail
from gateway.gateway.clients.mcp.mcp_client import ToolInfo
from gateway.gateway.clients.a2a.a2a_client import SkillInfo


def mcp_tool_to_capability(
    server_name: str,
    tool_info: ToolInfo,
) -> Capability:
    """
    Convert MCP ToolInfo to a unified Capability representation.
    Args:
        server_name: Name of the MCP server exposing the tool.
        tool_info: ToolInfo object from MCP client.
    Returns:
        Capability: Unified capability representation.
    """
    return Capability(
        capability_id=tool_info.tool_name,  # Using tool name as capability ID
        capability_name=tool_info.tool_name,
        source_name=server_name,
        kind="tool",
        summary=tool_info.description, # TODO Using description as summary for now
        description=tool_info.description,
        input_type="object",
        input_schema=tool_info.input_schema,
        output_type="object",
        output_schema=tool_info.output_schema,
    )

def a2a_skill_to_capability(
    agent_name: str,
    skill_info: SkillInfo,
) -> Capability:
    """
    Convert A2A SkillInfo to a unified Capability representation.
    Args:
        agent_name: Name of the agent exposing the skill.
        skill_info: SkillInfo object from A2A client.
    Returns:
        Capability: Unified capability representation.
    """
    return Capability(
        capability_id=skill_info.skill_id,  # Using skill ID as capability ID
        capability_name=skill_info.skill_name,
        source_name=agent_name,
        kind="agent",
        summary=skill_info.skill_description, # TODO Using just skill description as summary for now
        description=f"Agent {skill_info.agent_name} description \n {skill_info.agent_description} \n Skill description \n {skill_info.skill_description} and examples: {skill_info.skill_examples}",
        input_type="text",
        input_schema=None,
        output_type="text",
        output_schema=None,
    )

def capability_to_summary(capability: Capability) -> CapabilitySummary:
    """
    Convert a Capability to its summary representation.
    Args:
        capability: Capability object to summarize.
    Returns:
        CapabilitySummary: Summary representation of the capability.
    """
    return CapabilitySummary(
        capability_id=capability.capability_id,
        summary=capability.summary,
    )

def capability_to_detailed_summary(capability: Capability) -> CapabilityDetail:
    """
    Convert a Capability to its detailed summary representation.
    Args:
        capability: Capability object to summarize.
    Returns:
        CapabilityDetail: Detailed summary representation of the capability.
    """
    return CapabilityDetail(
        capability_id=capability.capability_id,
        description=capability.description,
        input_type=capability.input_type,
        input_schema=capability.input_schema,
        output_type=capability.output_type,
        output_schema=capability.output_schema,
    )