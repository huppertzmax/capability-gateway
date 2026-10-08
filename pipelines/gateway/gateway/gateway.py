"""
ASGI entrypoint for the gateway service.
"""
from __future__ import annotations
import logging
import click
import uvicorn
from gateway.gateway.gateway_application import create_app
from shared.logging_utils import configure_component_logger, mirror_handlers_to_root

logger = configure_component_logger(
    logger_name=__name__,
    component_group="gateway",
    component_name="gateway",
    console_level=logging.INFO,
    file_level=logging.DEBUG,
    console_tag="gateway-entry-console",
    file_tag="gateway-entry-file",
    file_mode="a",
)
mirror_handlers_to_root(
    logger=logger,
    level=logging.WARNING,
)

app = None


@click.command()
@click.option("--port", default=8000, help="Port to run the gateway on")
@click.option("--host", default="localhost", help="Host to run the gateway on")
@click.option("--mode", default="standard", help="Operation mode (e.g. RAG)")
def main(port: int, host: str, mode: str):
    """Run the gateway ASGI app."""
    logger.info("GATEWAY → start url=http://%s:%s mode=%s", host, port, mode)
    global app
    app = create_app(mode=mode)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
