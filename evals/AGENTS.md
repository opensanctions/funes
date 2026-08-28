# Extraction eval guidance

These evals define the ground truth used to judge the extraction pipeline. They
must remain an independent check on the runtime prompt: do not treat the current
wording in `funes/extract.py` as proof that an expectation is correct, and do not
change an expectation merely to make the current model pass.

Keep this file focused on fixture-authoring and evaluation decisions. Do not copy
the extraction prompt here. When prompt and fixture disagree, review the captured
source, the inspection brief, and the intended product semantics before changing
either one.

## Fixture ground truth

- A case consists of a frozen capture plus a trusted, brief-scoped request. The
  brief selects relevant relationships but does not itself establish page facts.
- Include every source-supported, in-scope person-position holding, including
  historical, future, former, or incidental mentions. Merge duplicate mentions
  of the same holding, but retain multiple explicitly stated holdings.
- Preserve source wording exactly for evaluated fields, including capitalization,
  punctuation, language, and displayed honorifics. Downstream normalization and
  deduplication belong to Zavod.
- Document titles and meta descriptions are valid source evidence.
- An organization explicitly scoping its own staff or positions may supply the
  organization for entries beneath it. Do not apply the page organization when
  the page aggregates multiple organizations, such as unnamed partner
  representatives.
- `jurisdiction` is a source-stated geographic area associated with a holding. It
  may be an office's geographic scope, a constituency, or a geographic home
  authority. An organization, employer, or institution name is not a
  jurisdiction.
- Extract countries only from unambiguous nationality or citizenship statements.
  Origin, residence-like wording, nearby geographic labels, and demonym
  adjectives are insufficient.
- In an appointment or term roster, a lone year attached to a holding may be
  treated as its start date. A stated range supplies start and end dates.

Outcome classification and evidence boundaries are implemented by the extraction
contract in `funes/extract.py`. Blocker fixtures are intentional regression tests
of the repair-versus-terminal-miss boundary, even when they closely resemble an
explicit runtime rule.

## Evaluation contract

- `Hit` expectations require an exact, order-independent person-position graph.
  Names, dates of birth, countries, position names, organizations, jurisdictions,
  start dates, end dates, populated values, and nulls must all agree.
- `Person.bio` and `Position.description` are informational and are not part of
  exact matching because multiple verbatim excerpts may be equally valid.
- Miss and broken expectations assert the result kind only. Their reason text is
  useful ground-truth documentation, but semantically equivalent prose is too
  variable to compare reliably.
- F1 and per-field accuracy scores are diagnostics. They help explain a failed
  exact hit assertion; they are not substitutes for it.

## Maintaining fixtures

- Prefer small captures that isolate one behavior, while retaining enough page
  context to make the expected interpretation defensible.
- Paired cases using one capture with different briefs are especially useful for
  testing scope independently of page understanding.
- Add messy and less literal cases over time, but retain simple policy regression
  cases for important repair and miss behavior.
- Any ground-truth change should be justified by source evidence or an explicit
  product decision, not by model performance during prompt optimization.
