"""Tests for the discovery eval dataset, evaluator, and task wiring.

No real model requests are made.
"""

import asyncio
import json
from types import NoneType

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_evals import Dataset
from pydantic_evals.evaluators import EvaluatorContext

from evals.evaluators import CUSTOM_EVALUATOR_TYPES, DiscoveryLinkSet
from evals.models import FixtureInput
from evals.task import FIXTURES, discover
from funes.discovery import Discovery, LinkSelection, page_link_urls

DATASET_PATH = "evals/datasets/discovery.yaml"


@pytest.fixture(scope="module")
def dataset() -> Dataset[FixtureInput, Discovery, NoneType]:
    return Dataset[FixtureInput, Discovery, NoneType].from_file(
        DATASET_PATH, custom_evaluator_types=CUSTOM_EVALUATOR_TYPES
    )


def test_dataset_loads_with_direct_hit_cases(dataset):
    assert dataset.evaluators == [DiscoveryLinkSet()]
    assert {case.name for case in dataset.cases} == {
        "governance_hub_to_holder_pages",
        "secretariat_team_amid_out_of_scope_links",
        "secretary_general_profile_to_deputies",
        "court_council_to_members_and_secretary_general",
        "current_profile_to_directors_and_former_holders",
        "parent_secretariat_scope",
        "sibling_program_scope",
        "profile_links_board_scope",
        "profile_links_advisory_scope",
        "holders_not_candidates",
    }


def test_expected_urls_are_distinct_links_from_the_fixture(dataset):
    for case in dataset.cases:
        inputs = case.inputs
        html = (FIXTURES / f"{inputs.fixture}.html").read_text(encoding="utf-8")
        available = page_link_urls(inputs.final_url or inputs.url, html)
        expected = [selection.url for selection in case.expected_output.selections]
        assert len(expected) == len(set(expected))
        assert set(expected) <= available


def _ctx(
    expected: Discovery, output: Discovery
) -> EvaluatorContext[FixtureInput, Discovery]:
    return EvaluatorContext(
        name="discovery",
        inputs=FixtureInput(
            fixture="x",
            url="https://x.example.org/",
            people_sought="Board members",
            subject_label="Organization",
            subject="Example Foundation",
        ),
        metadata=None,
        expected_output=expected,
        output=output,
        duration=0.0,
        _span_tree=None,
        attributes={},
        metrics={},
    )


def test_evaluator_exact_match_ignores_order_and_reasons():
    expected = Discovery(
        selections=[
            LinkSelection(url="https://example.org/a", reason="Expected A."),
            LinkSelection(url="https://example.org/b", reason="Expected B."),
        ]
    )
    output = Discovery(
        selections=[
            LinkSelection(url="https://example.org/b", reason="Different reason."),
            LinkSelection(url="https://example.org/a", reason="Also different."),
        ]
    )

    scores = DiscoveryLinkSet().evaluate(_ctx(expected, output))

    assert scores["links_match"].value
    assert scores["link_precision"] == 1.0
    assert scores["link_recall"] == 1.0
    assert scores["link_f1"] == 1.0


def test_evaluator_reports_missing_and_extra_links():
    expected = Discovery(
        selections=[
            LinkSelection(url="https://example.org/a", reason="Expected A."),
            LinkSelection(url="https://example.org/b", reason="Expected B."),
        ]
    )
    output = Discovery(
        selections=[
            LinkSelection(url="https://example.org/a", reason="Selected A."),
            LinkSelection(url="https://example.org/c", reason="Extra C."),
        ]
    )

    scores = DiscoveryLinkSet().evaluate(_ctx(expected, output))

    assert not scores["links_match"].value
    assert scores["link_precision"] == 0.5
    assert scores["link_recall"] == 0.5
    assert scores["link_f1"] == 0.5


def test_eval_task_uses_runtime_prompt_and_filters_unknown_urls():
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
                            "selections": [
                                {
                                    "url": "https://www.asteria.example.org/governance/president-and-council",
                                    "reason": "The link is presented beside an image of Council members.",
                                },
                                {
                                    "url": "https://hallucinated.example/board",
                                    "reason": "This URL is absent from the fixture.",
                                },
                            ]
                        }
                    )
                )
            ]
        )

    inputs = FixtureInput(
        fixture="discovery_governance_hub",
        url="https://www.asteria.example.org/about/governance",
        people_sought="Director-General, deputy heads, and members of the governing Council",
        subject_label="Organization",
        subject="Asteria Conservation Union",
    )
    result = asyncio.run(discover(inputs, model=FunctionModel(fn)))

    assert [selection.url for selection in result.selections] == [
        "https://www.asteria.example.org/governance/president-and-council"
    ]
    assert captured["instructions"].endswith(
        "People sought: Director-General, deputy heads, and members of the governing Council\n"
        "Organization: Asteria Conservation Union"
    )
    [prompt] = [
        content for content in captured["contents"] if "<page_snapshot>" in content
    ]
    assert "How the Union is governed" in prompt
    assert (
        "[href=https://www.asteria.example.org/governance/president-and-council]"
        in prompt
    )
