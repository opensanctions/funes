"""Offline evaluation of the extraction agent against frozen HTML fixtures.

Fixtures are fictional pages structurally equivalent to real captures (see
fixtures/); ground truth is labeled by hand. Unlike the pytest suite, this
hits a real model and costs tokens — run it deliberately:

    uv run python evals/run.py [--model NAME]

Exit status is nonzero when any case fails an assertion.
"""

import argparse
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from pydantic import BaseModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from funes.config import load_config
from funes.extract import (
    Extraction,
    ExtractionDependencies,
    PageResult,
    Person,
    Position,
    build_extraction_agent,
    build_prompt,
    metadata_from_html,
)
from funes.outline import build_outline

FIXTURES = Path(__file__).parent / "fixtures"


class FixtureInput(BaseModel):
    """One frozen page: the fixture file stem and its fictional final URL."""

    fixture: str
    url: str


async def extract(inputs: FixtureInput, model: str) -> PageResult:
    """Run the real extraction pipeline (minus capture) over one fixture."""
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


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _persons(extraction: Extraction) -> set[str]:
    return {_norm(p.name) for p in extraction.persons}


def _pairs(extraction: Extraction) -> set[tuple[str, str]]:
    return {
        (_norm(p.name), _norm(pos.name))
        for p in extraction.persons
        for pos in p.positions
    }


def _f1(expected: set, actual: set) -> float:
    if not expected:
        return 1.0 if not actual else 0.0
    if not actual:
        return 0.0
    tp = len(expected & actual)
    if tp == 0:
        return 0.0
    precision = tp / len(actual)
    recall = tp / len(expected)
    return 2 * precision * recall / (precision + recall)


@dataclass
class ExtractionF1(Evaluator[FixtureInput, PageResult]):
    """Score extracted persons and person-position pairs against ground truth.

    Comparison is on normalized names only; organization, dates, and other
    fields are deliberately unscored. Emits one assertion (exact person set)
    and two scores (person F1, person-position pair F1).
    """

    def evaluate(
        self, ctx: EvaluatorContext[FixtureInput, PageResult]
    ) -> dict[str, EvaluationReason | float]:
        expected = ctx.expected_output
        assert isinstance(expected, Extraction)
        if not isinstance(ctx.output, Extraction):
            return {
                "persons_match": EvaluationReason(
                    False, reason=f"output is {ctx.output.kind!r}, not an extraction"
                ),
                "person_f1": 0.0,
                "person_position_f1": 0.0,
            }
        expected_persons = _persons(expected)
        actual_persons = _persons(ctx.output)
        return {
            "persons_match": EvaluationReason(
                expected_persons == actual_persons,
                reason=(
                    f"missing={sorted(expected_persons - actual_persons)} "
                    f"extra={sorted(actual_persons - expected_persons)}"
                ),
            ),
            "person_f1": _f1(expected_persons, actual_persons),
            "person_position_f1": _f1(_pairs(expected), _pairs(ctx.output)),
        }


dataset = Dataset[FixtureInput, PageResult, None](
    name="extraction",
    cases=[
        Case(
            name="office_of_the_sg",
            inputs=FixtureInput(
                fixture="office_of_the_sg",
                url="https://www.caf.example.org/about/office-of-the-sg",
            ),
            expected_output=Extraction(
                persons=[
                    Person(
                        name="Mirela Voss",
                        positions=[Position(name="Secretary-General")],
                    ),
                    Person(
                        name="Tomas Lindqvist",
                        positions=[Position(name="Chef de Cabinet")],
                    ),
                ]
            ),
        ),
    ],
    evaluators=[ExtractionF1()],
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", default=None, help="model name; defaults to OPENAI_MODEL from .env"
    )
    args = parser.parse_args()
    model = args.model or load_config().model.name

    report = dataset.evaluate_sync(
        partial(extract, model=model), name=f"extraction:{model}", progress=False
    )
    report.print(include_input=True, include_output=True, include_durations=True)

    failed = any(
        result.value is False
        for case in report.cases
        for result in case.assertions.values()
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
