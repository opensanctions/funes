"""Custom evaluators for the extraction eval suite.

Classes referenced from dataset YAML must also be listed in
CUSTOM_EVALUATOR_TYPES and passed to both ``Dataset.from_file`` and
``Dataset.to_file``.
"""

from dataclasses import dataclass

from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from evals.models import FixtureInput
from funes.extract import BrokenPage, Extraction, PageResult


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
    fields are deliberately unscored. For a ``BrokenPage`` expectation, the
    sole check is that the output is also broken with a stated reason. Emits
    one assertion (exact person set)
    and two scores (person F1, person-position pair F1).
    """

    def evaluate(
        self, ctx: EvaluatorContext[FixtureInput, PageResult]
    ) -> dict[str, EvaluationReason | float]:
        expected = ctx.expected_output
        if isinstance(expected, BrokenPage):
            return {
                "broken_match": EvaluationReason(
                    isinstance(ctx.output, BrokenPage)
                    and bool(ctx.output.reason.strip()),
                    reason=(
                        f"output is {ctx.output.kind!r}, expected broken"
                        if not isinstance(ctx.output, BrokenPage)
                        else "reason stated"
                    ),
                )
            }
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


CUSTOM_EVALUATOR_TYPES = [ExtractionF1]
