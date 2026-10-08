"""
Agent card configuration loading by standards of A2A protocol.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field, ValidationError

from a2a.types import AgentCard, AgentSkill, AgentCapabilities


class SkillConfig(BaseModel):
    """Definition of a single agent skill loaded from JSON."""

    id: str
    name: str
    description: str
    tags: List[str] = Field(default_factory=list)
    examples: Optional[List[str]] = None


class AgentConfig(BaseModel):
    """Top-level agent config file structure."""

    agent_id: str
    name: str
    description: str
    version: str = "1.0.0"
    url: Optional[str] = None
    default_input_modes: List[str] = Field(default_factory=list)
    default_output_modes: List[str] = Field(default_factory=list)
    capabilities: Optional[dict] = None
    skills: List[SkillConfig]


def _validate_and_create_agent_skills(skills: List[SkillConfig]) -> List[AgentSkill]:
    """
    Validate and convert SkillConfig objects to A2A AgentSkill objects.
    
    Args:
        skills: List of SkillConfig objects to validate and convert.
    Returns:
        List[AgentSkill]: List of validated A2A AgentSkill objects.
    Raises:
        ValueError: When skill validation fails.
    """
    seen: set[str] = set()
    for skill in skills:
        if not skill.id.strip():
            raise ValueError("SkillConfig.id must be non-empty")
        if skill.id in seen:
            raise ValueError(f"Duplicate skill id found: {skill.id}")
        seen.add(skill.id)
        if not skill.name.strip():
            raise ValueError(f"Skill '{skill.id}' has an empty name")
        if not skill.description.strip():
            raise ValueError(f"Skill '{skill.id}' has an empty description")
        if skill.examples is not None and len(skill.examples) > 5:
            raise ValueError(f"Skill '{skill.id}' has too many examples (max 5)")
    
    # Convert skills to A2A AgentSkill objects
    agent_skills = [
        AgentSkill(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            tags=skill.tags,
            examples=skill.examples,
        )
        for skill in skills
    ]
    
    return agent_skills


def load_agent_card_config(config_path: Path) -> AgentCard:
    """
    Load and validate an agent card configuration from JSON file.
    Returns a fully initialized A2A protocol AgentCard with AgentSkill objects.
    
    Args:
        config_path: Path to the JSON file.
    Returns:
        AgentCard: A2A protocol AgentCard object with initialized skills.
    Raises:
        FileNotFoundError: When the file is missing.
        ValueError: When the file content is invalid.
    """
    resolved_path = Path(config_path)
    try:
        raw = json.loads(resolved_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Agent card config file not found: {resolved_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Agent card config file is not valid JSON: {resolved_path}") from exc
    
    # Parse the config file
    try:
        config = AgentConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Agent card config file validation failed: {resolved_path}") from exc
    
    # Validate and create AgentSkill objects
    agent_skills = _validate_and_create_agent_skills(config.skills)
    
    # Parse capabilities
    capabilities = AgentCapabilities(
        streaming=config.capabilities.get("streaming", False) if config.capabilities else False
    )
    
    # Create and return A2A AgentCard
    agent_card = AgentCard(
        name=config.name,
        description=config.description,
        url=config.url,
        version=config.version,
        default_input_modes=config.default_input_modes,
        default_output_modes=config.default_output_modes,
        capabilities=capabilities,
        skills=agent_skills,
    )
    
    return agent_card
