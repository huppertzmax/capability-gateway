"""
FastAPI server for the notification agent.

This agent is a pydantic.ai LLM agent that uses the Gateway MCP endpoint to
call the mock notification tool. It receives natural-language handoffs and
keeps all communication in natural language.
"""

from __future__ import annotations
import click
from pathlib import Path
import uvicorn
from dotenv import load_dotenv
from shared.config import PipelineConfig
from shared.agent_logging import get_agent_logger
from gateway.agents.agent import create_agent_with_mcp_gateway_connection, load_system_prompt
from gateway.agents.agent_server import create_a2a_agent_app, create_agent_executor

load_dotenv()

AGENT_ID = "notification_agent"
PIPELINES_FOLDER_ROOT = Path(__file__).resolve().parents[3]  # Navigate to pipelines/
AGENT_CARD_CONFIG_PATH = PIPELINES_FOLDER_ROOT / "shared" / "configs" / "notification_agent_card.json"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = PROJECT_ROOT / "output" / "runs"

@click.command()
@click.option("--port", default=PipelineConfig.NOTIFICATION_AGENT_PORT, help="Port to run the agent on")
@click.option("--host", default="localhost", help="Host to run the agent on")
@click.option("--mode", default="standard", help="Operation mode (e.g. RAG)")
@click.option("--gateway-url", default=PipelineConfig.GATEWAY_MCP_URL, help="URL of the Gateway MCP")
@click.option("--model", default=PipelineConfig.OPENAI_MODEL, help="Model name to use")
def main(port: int, host: str, mode: str | None, gateway_url: str, model: str):
    agent_logger = get_agent_logger(AGENT_ID)
    agent_logger.logger.info(f"Starting notification_agent server on http://{host}:{port}")
    base_prompt_path = Path(__file__).parent / "config" / "system_prompt.txt"
    rag_addendum_path = Path(__file__).parent / "config" / "system_prompt_rag.txt"
    system_prompt = load_system_prompt(
        base_prompt_path,
        agent_logger.logger,
        addendum_path=rag_addendum_path if mode == "RAG" else None,
    )
    new_agent = create_agent_with_mcp_gateway_connection(
        model_name=model,
        system_prompt=system_prompt,
        gateway_url=gateway_url,
        agent_id=AGENT_ID,
        agent_logger=agent_logger,
    )
    new_executor = create_agent_executor(new_agent, agent_logger, RUNS_ROOT)
    new_app = create_a2a_agent_app(
        agent_card_config_path=AGENT_CARD_CONFIG_PATH,
        agent_executor=new_executor,
        override_url=f"http://{host}:{port}/"
    )
    uvicorn.run(
        new_app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )

if __name__ == "__main__":
    main()
