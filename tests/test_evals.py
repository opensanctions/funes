"""Tests for the inspection eval suite: dataset validity, evaluator scoring,
and the eval task's prompt API. No model requests are made: the eval task runs
against a pydantic-ai FunctionModel that captures the prompt."""

import asyncio
import json
from types import NoneType

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
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
)

DATASET_PATH = "evals/datasets/extraction.yaml"


def _ctx(expected: PageResult, output: PageResult) -> EvaluatorContext:
    return EvaluatorContext(
        name="test",
        inputs=FixtureInput(
            fixture="x",
            url="https://x.example.org/",
            people_sought="Judges",
            subject_label="Court",
            subject="Example Court",
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


def test_fixture_inputs_fail_loudly_on_unknown_or_blank_fields():
    with pytest.raises(ValidationError):
        FixtureInput(
            fixture="x",
            url="https://x.example.org/",
            people_sought="Judges",
            subject_label="Court",
            subject=" ",
        )
    with pytest.raises(ValidationError):
        FixtureInput(
            fixture="x",
            url="https://x.example.org/",
            people_sought="Judges",
            subject_label="Court",
            subject="Example Court",
            unexpected=True,
        )


def test_dataset_loads_with_briefs_and_result_types(dataset):
    assert dataset.evaluators == [InspectionF1()]
    kinds = set()
    for case in dataset.cases:
        assert case.inputs.people_sought.strip()
        assert case.inputs.subject_label.strip()
        assert case.inputs.subject.strip()
        kinds.add(case.expected_output.kind)
    assert kinds == {"hit", "miss", "broken"}


def test_hit_expectations_carry_brief_scoped_graphs(dataset):
    by_name = {case.name: case for case in dataset.cases}
    for case in dataset.cases:
        expected = case.expected_output
        if isinstance(expected, Hit):
            assert expected.persons
            for person in expected.persons:
                assert person.positions
    conjoined = by_name["conjoined_role_misattribution"].expected_output
    assert isinstance(conjoined, Hit)
    assert {p.name for p in conjoined.persons} == {
        "Adama Kessi",
        "Omar Nasri",
        "Lucas Marek",
    }
    assert all(
        pos.name == "Chairperson of the Veltrane Union"
        for p in conjoined.persons
        for pos in p.positions
    )


# --- evaluator behavior ---


def test_evaluator_broken_and_miss_expectations():
    evaluator = InspectionF1()
    for expected in (
        BrokenSnapshot(reason="challenge page"),
        Miss(reason="no holders"),
    ):
        wrongs = (
            Miss(reason="nothing here"),
            BrokenSnapshot(reason="challenge"),
            _hit(Person(name="A", positions=[Position(name="Director")])),
        )
        for actual in wrongs:
            if actual.kind == expected.kind:
                continue
            (reason,) = evaluator.evaluate(_ctx(expected, actual)).values()
            assert not reason.value
        (reason,) = evaluator.evaluate(
            _ctx(expected, expected.__class__(reason="different prose"))
        ).values()
        assert reason.value


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
    assert not scores["hit_match"].value
    assert scores["person_f1"] == pytest.approx(2 * 1 * 1 / (1 + 2))
    assert scores["person_position_f1"] == pytest.approx(2 * 1 * 1 / (1 + 2))
    assert scores["organization_accuracy"] == 1.0


def test_evaluator_hit_exact_match_ignores_order_and_informational_prose():
    expected = _hit(
        Person(
            name="A",
            dob="1970",
            bio="Expected biography.",
            countries=["North", "South"],
            positions=[
                Position(
                    name="Chair",
                    organization="Council",
                    description="Expected responsibilities.",
                    jurisdiction="North",
                    start_date="2020",
                    end_date="2024",
                ),
                Position(name="Member", organization="Council"),
            ],
        )
    )
    actual = _hit(
        Person(
            name="A",
            dob="1970",
            bio="Different but valid biography.",
            countries=["South", "North"],
            positions=[
                Position(name="Member", organization="Council"),
                Position(
                    name="Chair",
                    organization="Council",
                    description="Different but valid responsibilities.",
                    jurisdiction="North",
                    start_date="2020",
                    end_date="2024",
                ),
            ],
        )
    )

    scores = InspectionF1().evaluate(_ctx(expected, actual))

    assert scores["hit_match"].value


def test_evaluator_hit_qualifier_difference_fails_exact_match():
    expected = _hit(
        Person(
            name="A",
            positions=[Position(name="Chair", organization="Expected Council")],
        )
    )
    actual = _hit(
        Person(
            name="A",
            positions=[Position(name="Chair", organization="Other Council")],
        )
    )

    scores = InspectionF1().evaluate(_ctx(expected, actual))

    assert not scores["hit_match"].value
    assert scores["person_f1"] == 1.0
    assert scores["person_position_f1"] == 1.0
    assert scores["organization_accuracy"] == 0.0


def test_evaluator_hit_non_hit_output_scores_zero():
    expected = _hit(
        Person(name="A", positions=[Position(name="Director")]),
    )
    for wrong in (Miss(reason="no holders"), BrokenSnapshot(reason="challenge")):
        scores = InspectionF1().evaluate(_ctx(expected, wrong))
        assert not scores["hit_match"].value
        assert scores["person_f1"] == 0.0
        assert scores["person_position_f1"] == 0.0
        assert scores["organization_accuracy"] == 0.0


# --- eval task prompt API ---


def test_extract_separates_trusted_brief_from_page_prompt():
    """extract() sends the brief as instructions and the page as user content."""
    import evals.task as task_module

    captured = {}

    def fn(messages, info):
        captured["contents"] = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(getattr(part, "content", None), str)
        ]
        captured["instructions"] = info.instructions
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "result": {
                                "kind": "Miss",
                                "data": {"kind": "miss", "reason": "stubbed"},
                            }
                        }
                    )
                )
            ]
        )

    inputs = FixtureInput(
        fixture="office_of_the_sg",
        url="https://www.caf.example.org/about/office-of-the-sg",
        people_sought="Leadership of the Office of the Secretary-General",
        subject_label="Organization",
        subject="CAF",
    )
    result = asyncio.run(task_module.extract(inputs, model=FunctionModel(fn)))
    assert isinstance(result, Miss)
    assert captured["instructions"].endswith(
        "People sought: Leadership of the Office of the Secretary-General\n"
        "Organization: CAF"
    )
    [prompt] = [
        content for content in captured["contents"] if "<page_snapshot>" in content
    ]
    assert "<objective>" not in prompt
    assert "office-of-the-sg" in prompt
