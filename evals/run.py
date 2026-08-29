"""Run the inspection eval suite against frozen HTML fixtures.

Datasets live in datasets/ as YAML and are validated from their Pydantic
models when loaded; fixtures are fictional pages structurally equivalent to
real captures. Unlike the pytest suite, this hits a real model and costs tokens —
run it deliberately:

    uv run --env-file .env python -m evals.run [--dataset PATH] [--model NAME]
        [--case NAME ...]

Exit status is nonzero when a case fails, raises, or an evaluator crashes.
"""

import argparse
from functools import partial
from pathlib import Path
from types import NoneType

from pydantic_evals import Dataset

from evals.evaluators import CUSTOM_EVALUATOR_TYPES
from evals.models import FixtureInput
from evals.task import extract
from funes.config import load_config
from funes.extract import PageResult

DEFAULT_DATASET = Path(__file__).parent / "datasets" / "extraction.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=DEFAULT_DATASET, help="dataset YAML file"
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model id (provider:model); defaults to MODEL from the environment",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        metavar="NAME",
        help="run only this case; may be supplied more than once",
    )
    args = parser.parse_args()
    model = args.model or load_config().model.name

    dataset = Dataset[FixtureInput, PageResult, NoneType].from_file(
        args.dataset, custom_evaluator_types=CUSTOM_EVALUATOR_TYPES
    )
    if args.case:
        requested = set(args.case)
        available = {case.name for case in dataset.cases}
        if unknown := requested - available:
            parser.error("unknown case(s): " + ", ".join(sorted(unknown)))
        dataset.cases = [case for case in dataset.cases if case.name in requested]
    report = dataset.evaluate_sync(
        partial(extract, model=model), name=f"inspection:{model}", progress=False
    )
    report.print(include_input=True, include_output=True, include_durations=True)

    failed = bool(report.failures or report.report_evaluator_failures) or any(
        result.value is False
        for case in report.cases
        for result in case.assertions.values()
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
