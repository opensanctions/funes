"""Tests for the inspection eval suite: dataset validity, evaluator scoring,
and the eval task's prompt API. No model requests are made."""

from types import NoneType

import pytest
from pydantic_evals import Dataset
from pydantic_evals.evaluators import EvaluatorContext

from evals.evaluators import CUSTOM_EVALUATOR_TYPES, InspectionF1
from evals.models import FixtureInput
from funes.extract import (
    BrokenSnapshot,
    Hit,
    Miss,
    PageResult,
    Person,
    Position,
    build_prompt,
    metadata_from_html,
)

DATASET_PATH = "evals/datasets/extraction.yaml"


def _ctx(expected: PageResult, output: PageResult) -> EvaluatorContext:
    return EvaluatorContext(
        name="test",
        inputs=FixtureInput(
            fixture="x", url="https://x.example.org/", objective="test objective"
        ),
        metadata=None,
        expected_output=expected,
        output=output,
        duration=0.0,
        _span_tree=None,
        attributes={},
        metrics={},
    )


def _hit(*persons: Person) -> Hit:
    return Hit(persons=list(persons))


@pytest.fixture(scope="module")
def dataset() -> Dataset[FixtureInput, PageResult, NoneType]:
    return Dataset[FixtureInput, PageResult, NoneType].from_file(
        DATASET_PATH, custom_evaluator_types=CUSTOM_EVALUATOR_TYPES
    )


# --- dataset shape ---


def test_dataset_loads_with_objectives_and_result_types(dataset):
    assert dataset.cases
    kinds = set()
    for case in dataset.cases:
        assert case.inputs.objective.strip()
        assert case.inputs.objective not in {"", "test"}
        kinds.add(case.expected_output.kind)
    assert dataset.evaluators == [InspectionF1()]
    assert kinds == {"hit", "miss", "broken"}


def test_hit_expectations_carry_person_position_graphs(dataset):
    for case in dataset.cases:
        expected = case.expected_output
        if isinstance(expected, Hit):
            assert expected.persons
            for person in expected.persons:
                assert person.positions
    names = {
        case.name: {p.name for p in case.expected_output.persons}
        for case in dataset.cases
        if isinstance(case.expected_output, Hit)
    }
    # Objective-scoped: only Veltrane Union chairpersons, no Boris.
    assert names["conjoined_role_misattribution"] == {
        "Adama Kessi",
        "Omar Nasri",
        "Lucas Marek",
    }
    assert all(
        pos.name == "Chairperson of the Veltrane Union"
        for p in dataset.cases[5].expected_output.persons  # conjoined case
        for pos in p.positions
    )


# --- evaluator behavior ---


def test_evaluator_broken_expectation():
    scores = InspectionF1().evaluate(
        _ctx(BrokenSnapshot(reason="challenge page"), BrokenSnapshot(reason="bork"))
    )
    (reason,) = scores.values()
    assert reason.value

    for wrong in (
        Miss(reason="nothing here"),
        _hit(Person(name="A", positions=[Position(name="Director")])),
    ):
        scores = InspectionF1().evaluate(
            _ctx(BrokenSnapshot(reason="challenge page"), wrong)
        )
        (reason,) = scores.values()
        assert not reason.value


def test_evaluator_miss_expectation():
    scores = InspectionF1().evaluate(
        _ctx(Miss(reason="no holders"), Miss(reason="election notices only"))
    )
    (reason,) = scores.values()
    assert reason.value

    for wrong in (
        BrokenSnapshot(reason="challenge"),
        _hit(Person(name="A", positions=[Position(name="Director")])),
    ):
        scores = InspectionF1().evaluate(_ctx(Miss(reason="no holders"), wrong))
        (reason,) = scores.values()
        assert not reason.value


def test_evaluator_hit_scores_person_position_graph():
    expected = _hit(
        Person(
            name="Adama Kessi",
            positions=[Position(name="Chairperson", organization="Veltrane Union")],
        )
    )
    actual = _hit(
        Person(
            name="adama kessi",
            positions=[Position(name="Chairperson", organization="Veltrane Union")],
        ),
        Person(
            name="Extra Person",
            positions=[Position(name="Chairperson", organization="Veltrane Union")],
        ),
    )
    scores = InspectionF1().evaluate(_ctx(expected, actual))
    assert not scores["persons_match"].value  # extra person
    assert scores["person_f1"] == pytest.approx(2 * 1 * 1 / (1 + 2))
    assert scores["person_position_f1"] == pytest.approx(2 * 1 * 1 / (1 + 2))
    assert scores["organization_accuracy"] == 1.0


def test_evaluator_hit_non_hit_output_scores_zero():
    expected = _hit(
        Person(name="A", positions=[Position(name="Director")]),
    )
    for wrong in (Miss(reason="no holders"), BrokenSnapshot(reason="challenge")):
        scores = InspectionF1().evaluate(_ctx(expected, wrong))
        assert not scores["persons_match"].value
        assert scores["person_f1"] == 0.0
        assert scores["person_position_f1"] == 0.0
        assert scores["organization_accuracy"] == 0.0


# --- eval task prompt API ---


@pytest.mark.asyncio
def test_extract_builds_objective_scoped_prompt(monkeypatch):
    """extract() feeds inputs.objective into build_prompt without model calls."""
    import asyncio

    captured = {}

    class StubAgent:
        async def run(self, prompt, deps=None):
            captured["prompt"] = prompt
            captured["deps"] = deps

            class _Result:
                output = Miss(reason="stubbed")

            return _Result()

    import evals.task as task_module
    from funes.extract import build_extraction_agent as real_build

    monkeypatch.setattr(
        task_module, "build_extraction_agent", lambda model: StubAgent()
    )
    inputs = FixtureInput(
        fixture="office_of_the_sg",
        url="https://www.caf.example.org/about/office-of-the-sg",
        objective="Identify the leadership of the CAF Office of the Secretary-General.",
    )
    result = asyncio.run(task_module.extract(inputs, model="test:model"))
    assert isinstance(result, Miss)
    assert "<objective>" in captured["prompt"]
    assert inputs.objective in captured["prompt"]
    assert "office-of-the-sg" in captured["prompt"]
    assert real_build is not None  # real agent builder untouched


def test_build_prompt_requires_nonempty_objective():
    metadata = metadata_from_html(
        "https://x.example.org/",
        "<html></html>",
        final_url="https://x.example.org/",
        http_status=200,
    )
    with pytest.raises(ValueError):
        build_prompt("   ", metadata, "outline")
