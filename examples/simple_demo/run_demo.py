#!/usr/bin/env python3
"""Run the simple AURORA demo end-to-end.

This script demonstrates the full ideation-to-paper pipeline using
deterministic mock LLM responses and mock computation tools.  It
requires no API keys and runs fully offline.

Usage::

    python examples/simple_demo/run_demo.py

Or equivalently::

    python -m aurora run --config examples/simple_demo/config.yaml
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from aurora.config import load_config
from aurora.core.engine import ResearchEngine
from aurora.core.registry import ComponentRegistry
from aurora.llm.router import LLMRouter
from aurora.llm.base import UsageTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("demo")


async def main() -> None:
    config_path = Path(__file__).parent / "config.yaml"
    logger.info("Loading configuration from %s", config_path)
    config = load_config(config_path)

    # Create the mock provider and inject it into the router
    from examples.simple_demo.mock_llm import create_demo_mock_provider
    mock = create_demo_mock_provider()

    # Build a router with the mock provider pre-registered
    tracker = UsageTracker()
    mock._usage_tracker = tracker
    router = LLMRouter(
        config=config,
        providers={"mock": mock},
        usage_tracker=tracker,
    )

    # Create the engine and inject the mock router
    engine = ResearchEngine(config=config)
    engine._router = router

    logger.info("=" * 60)
    logger.info("AURORA Simple Demo")
    logger.info("Campaign: %s", config.campaign_id)
    logger.info("Objective: %s", config.objective[:80])
    logger.info("=" * 60)

    result = await engine.run_campaign()

    # Print results
    logger.info("=" * 60)
    logger.info("CAMPAIGN RESULT")
    logger.info("=" * 60)
    logger.info("Status: %s", result.status)
    logger.info("Iterations: %d", len(result.iterations))
    logger.info("Follow-up cycles: %d", result.follow_up_cycles)
    logger.info("Wall time: %.1fs", result.total_wall_time_seconds)
    logger.info("Total LLM tokens: %d", result.total_llm_tokens)

    if result.proposal:
        logger.info("Proposal: %s", result.proposal.title)
    if result.manuscript_path:
        logger.info("Manuscript: %s", result.manuscript_path)

    logger.info("Summary: %s", result.summary)

    # Print usage stats
    usage = router.get_usage_summary()
    if usage:
        logger.info("\nToken usage by model:")
        for model, stats in usage.items():
            logger.info("  %s: %d tokens (%d calls)", model, stats["total_tokens"], stats["call_count"])

    logger.info("\n✅ Demo completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
