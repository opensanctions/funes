"""The eval task: run the real extraction pipeline (minus capture) on a fixture."""

from pathlib import Path

from evals.models import FixtureInput
from funes.extract import (
    ExtractionDependencies,
    PageResult,
    build_extraction_agent,
    build_prompt,
    metadata_from_html,
)
from funes.outline import build_outline

FIXTURES = Path(__file__).parent / "fixtures"


async def extract(inputs: FixtureInput, model: str) -> PageResult:
    """Run metadata/outline/prompt construction and the extraction agent."""
    html = (FIXTURES / f"{inputs.fixture}.html").read_text(encoding="utf-8")
    metadata = metadata_from_html(
        inputs.url, html, final_url=inputs.url, http_status=200
    )
    outline = build_outline(inputs.url, html)

    async def read_resource(body_path: str) -> bytes:
        raise LookupError(f"fixture {inputs.fixture!r} has no resource bodies")

    deps = ExtractionDependencies(read_resource=read_resource, resource_media_types={})
    agent = build_extraction_agent(model)
    result = await agent.run(build_prompt(metadata, outline), deps=deps)
    return result.output
