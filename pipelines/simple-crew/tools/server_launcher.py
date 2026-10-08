"""
Utilities to start A2A servers with shared CLI options and AgentCard construction.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
import click
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from shared.model_loader import ALLOWED_MODELS
from shared.config import PipelineConfig
from shared.logging_utils import configure_component_logger

logger = configure_component_logger(
    logger_name=__name__,
    component_group="simple",
    component_name="server_launcher",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="simple-server-launcher-console",
    file_tag="simple-server-launcher-file",
    file_mode="a",
)


def add_common_cli(default_port: int):
    """
    Decorator that adds common host/port/model/arch CLI options.
    """
    def decorator(func):
        func = click.option(
            '--arch',
            'arch',
            type=click.Choice(['crew', 'simple'], case_sensitive=False),
            default="simple",
            help='Pipeline architecture to use (crew|simple). Falls back to PIPELINE_ARCH or crew.',
        )(func)
        func = click.option(
            '--model',
            'model',
            type=click.Choice(ALLOWED_MODELS, case_sensitive=False),
            default=PipelineConfig.OPENAI_MODEL,
            help='Optional model override. Defaults to OPENAI_MODEL from .env.',
        )(func)
        func = click.option('--port', 'port', default=default_port)(func)
        func = click.option('--host', 'host', default='localhost')(func)
        return func
    return decorator


def build_agent_card(meta: Dict[str, Any], host: str, port: int, supported_modes: Sequence[str]) -> AgentCard:
    """
    Construct an AgentCard from a meta description.
    meta fields: name, description, version, skills (list of dicts with id,name,description,tags,examples)
    """
    capabilities = AgentCapabilities(streaming=False)
    skills = [
        AgentSkill(
            id=skill['id'],
            name=skill['name'],
            description=skill['description'],
            tags=skill.get('tags', []),
            examples=skill.get('examples', []),
        )
        for skill in meta.get('skills', [])
    ]
    logger.debug(
        f"Building AgentCard name={meta.get('name', '-')} host={host} port={port} skills={len(skills)} supported_modes={list(supported_modes)}"
    )
    return AgentCard(
        name=meta['name'],
        description=meta['description'],
        url=f'http://{host}:{port}/',
        version=meta.get('version', '1.0.0'),
        default_input_modes=supported_modes,
        default_output_modes=supported_modes,
        capabilities=capabilities,
        skills=skills,
    )


def start_agent_server(agent_card: AgentCard, executor_instance: Any, host: str, port: int) -> None:
    """
    Start the uvicorn server with the given AgentCard and executor.
    """
    logger.info(f"Starting A2A server name={agent_card.name} host={host} port={port}")
    request_handler = DefaultRequestHandler(
        agent_executor=executor_instance,
        task_store=InMemoryTaskStore(),
    )
    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    import uvicorn
    try:
        uvicorn.run(
            server.build(),
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
        logger.info(f"A2A server stopped name={agent_card.name} host={host} port={port}")
    except Exception as exc:
        logger.error(f"A2A Server failure name={agent_card.name} host={host} port={port} error={exc}", exc_info=True)
        raise


def load_card_meta(config_file: Path) -> Dict[str, Any]:
    """
    Load agent card metadata from config_file path.
    """
    try:
        with config_file.open('r', encoding='utf-8') as handle:
            card_meta = json.load(handle)
    except Exception as exc:
        logger.error(f"Error reading {config_file}: {exc}", exc_info=True)
        raise
    logger.debug(f"Loaded agent card config path={config_file}")
    return card_meta
