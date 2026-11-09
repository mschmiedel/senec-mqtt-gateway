"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys

from . import __version__
from .config import Config, ConfigError, load_plan
from .gateway import Gateway
from .schema import ReadPlan, merge_sections, render_all
from .senec import SenecClient, SenecError, create_session

_LOGGER = logging.getLogger("senec_mqtt_gateway")


async def _read_once(config: Config, plan: ReadPlan) -> int:
    """Read the SENEC once and print the values, without touching MQTT."""
    async with create_session() as session:
        senec = SenecClient(session, config.senec_url, plan=plan)
        await senec.start_session()
        info = await senec.read_device_info()
        try:
            raw = await senec.read_values()
        except SenecError as exc:
            print(f"SENEC not readable: {exc}", file=sys.stderr)
            return 1
        try:
            raw = merge_sections(raw, await senec.read_details())
        except SenecError as exc:
            print(f"Details not readable: {exc}", file=sys.stderr)
    print(json.dumps({"model": info.model, "version": info.version, "values": render_all(raw, plan)}, indent=2))
    return 0


async def _serve(config: Config, plan: ReadPlan) -> int:
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, task.cancel)

    async with create_session() as session:
        gateway = Gateway(config, SenecClient(session, config.senec_url, plan=plan))
        try:
            await gateway.run()
        except asyncio.CancelledError:
            _LOGGER.info("Stopped")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="senec-mqtt-gateway", description=__doc__)
    parser.add_argument("--once", action="store_true", help="read the SENEC once, print the values and exit")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    try:
        config = Config.from_env(os.environ)
        plan = load_plan(config.metrics_config)
    except ConfigError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for warning in plan.warnings():
        _LOGGER.warning(warning)
    return asyncio.run(_read_once(config, plan) if args.once else _serve(config, plan))


if __name__ == "__main__":
    sys.exit(main())
