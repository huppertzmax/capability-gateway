"""
Entry point for the A2A + CrewAI Matcher agent.
"""
import logging
import click
from pathlib import Path
from simple.agents.matcher_agent.agent import MatcherAgent
from simple.tools.base_executor import BaseAgentExecutor
from simple.tools.server_launcher import add_common_cli, build_agent_card, start_agent_server, load_card_meta
from shared.logging_utils import configure_component_logger

logger = configure_component_logger(
    logger_name=__name__,
    component_group="simple",
    component_name="matcher_agent_entrypoint",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="simple-matcher-entry-console",
    file_tag="simple-matcher-entry-file",
    file_mode="a",
)

PIPELINES_FOLDER_ROOT = Path(__file__).resolve().parents[3]  # Navigate to pipelines/
AGENT_CARD_CONFIG_PATH = PIPELINES_FOLDER_ROOT / "shared" / "configs" / "matcher_agent_card.json"

@click.command()
@add_common_cli(default_port=10002)
def main(host: str, port: int, model: str | None, arch: str | None) -> None:
    """Entry point for the A2A + CrewAI System Owner Matcher agent."""
    logger.info(f"Starting matcher_agent entrypoint host={host} port={port} model={model or '-'} arch={arch or '-'}")
    logger.debug(f"Using AgentCard config path={AGENT_CARD_CONFIG_PATH}")
    try:
        agent_card = build_agent_card(
            meta=load_card_meta(AGENT_CARD_CONFIG_PATH),
            host=host,
            port=port,
            supported_modes=MatcherAgent.SUPPORTED_CONTENT_TYPES,
        )
        executor = BaseAgentExecutor(
            agent_cls=MatcherAgent,
            agent_id="matcher_agent",
            model_name=model,
            architecture=arch,
            result_label='Matching result:',
        )
        logger.debug(f"Initialized executor for matcher_agent host={host} port={port}")
        logger.info(f"Launching matcher_agent server host={host} port={port}")
        start_agent_server(agent_card, executor, host=host, port=port)
        logger.info(f"matcher_agent server stopped host={host} port={port}")
    except Exception as error:
        logger.error(f'An error occurred during server startup: {error}', exc_info=True)
        exit(1)


if __name__ == '__main__':
    main()
