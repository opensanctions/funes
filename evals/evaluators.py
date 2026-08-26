"""Custom evaluators for the extraction eval suite.

Classes referenced from dataset YAML must also be listed in
CUSTOM_EVALUATOR_TYPES and passed to both ``Dataset.from_file`` and
``Dataset.to_file``.
"""

from dataclasses import dataclass
from typing import Any

from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from evals.models import FixtureInput
from funes.extract import BrokenPage, Extraction, PageResult, Person, Position


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _norm_optional(value: str | None) -> str | None:
    return None if value is None else _norm(value)


def _persons(extraction: Extraction) -> dict[str, Person]:
    return {_norm(p.name): p for p in extraction.persons}


def _positions(person: Person) -> dict[str, Position]:
    return {_norm(pos.name): pos for pos in person.positions}


def _pairs(extraction: Extraction) -> set[tuple[str, str]]:
    return {
        (_norm(p.name), _norm(pos.name))
        for p in extraction.persons
        for pos in p.positions
    }


def _f1(expected: set[Any], actual: set[Any]) -> float:
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


def _field_accuracy(
    matched: list[tuple[Any, Any]], field: str, has_expected: bool
) -> float:
    """Share of matched entities whose *field* agrees (normalized, None-safe).

    Vacuous when there is nothing to compare: 1.0 when the expectation itself
    carries no entities, else 0.0 (the expected entities were all missed, which
    the F1 scores already punish).
    """
    if not matched:
        return 0.0 if has_expected else 1.0
    agree = sum(
        _norm_optional(getattr(e, field)) == _norm_optional(getattr(a, field))
        for e, a in matched
    )
    return agree / len(matched)


def _countries_accuracy(
    matched: list[tuple[Person, Person]], has_expected: bool
) -> float:
    if not matched:
        return 0.0 if has_expected else 1.0
    agree = sum(
        {_norm(c) for c in e.countries} == {_norm(c) for c in a.countries}
        for e, a in matched
    )
    return agree / len(matched)


@dataclass
class ExtractionF1(Evaluator[FixtureInput, PageResult]):
    """Score an extraction against ground truth, structurally then per field.

    Assertions: exact normalized person set (``persons_match``), or, for a
    ``BrokenPage`` expectation, that the output is broken with a stated reason
    (``broken_match``). Scores: person F1, person-position pair F1, and — over
    persons and positions matched by normalized name — per-field accuracy for
    organization, jurisdiction, start_date, end_date, and countries. Field
    scores are informational, never assertions: a debatable label should show
    up as a sub-1.0 score, not a failed suite.
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
                "organization_accuracy": 0.0,
                "jurisdiction_accuracy": 0.0,
                "start_date_accuracy": 0.0,
                "end_date_accuracy": 0.0,
                "countries_accuracy": 0.0,
            }
        expected_persons = _persons(expected)
        actual_persons = _persons(ctx.output)
        matched_persons = [
            (expected_persons[name], actual_persons[name])
            for name in expected_persons.keys() & actual_persons.keys()
        ]
        matched_positions = [
            (expected_pos[name], actual_pos[name])
            for expected_person, actual_person in matched_persons
            for expected_pos, actual_pos in [
                (_positions(expected_person), _positions(actual_person))
            ]
            for name in expected_pos.keys() & actual_pos.keys()
        ]
        has_persons = bool(expected.persons)
        has_positions = bool(_pairs(expected))
        return {
            "persons_match": EvaluationReason(
                expected_persons.keys() == actual_persons.keys(),
                reason=(
                    f"missing={sorted(expected_persons.keys() - actual_persons.keys())} "
                    f"extra={sorted(actual_persons.keys() - expected_persons.keys())}"
                ),
            ),
            "person_f1": _f1(set(expected_persons), set(actual_persons)),
            "person_position_f1": _f1(_pairs(expected), _pairs(ctx.output)),
            "organization_accuracy": _field_accuracy(
                matched_positions, "organization", has_positions
            ),
            "jurisdiction_accuracy": _field_accuracy(
                matched_positions, "jurisdiction", has_positions
            ),
            "start_date_accuracy": _field_accuracy(
                matched_positions, "start_date", has_positions
            ),
            "end_date_accuracy": _field_accuracy(
                matched_positions, "end_date", has_positions
            ),
            "countries_accuracy": _countries_accuracy(matched_persons, has_persons),
        }


CUSTOM_EVALUATOR_TYPES = [ExtractionF1]
