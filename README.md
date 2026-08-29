# Funes

Political position holders are named across thousands of public websites. The information is public, but it is not really data: it lives in biographies, board pages, organization charts, PDFs, image-heavy rosters, and JavaScript applications.

Bespoke scrapers work for prominent sources, but they do not scale across the long tail of courts, regulators, councils, public bodies, and international organizations. This leaves a persistent gap in the coverage of politically exposed persons and other people in public-interest roles.

**Funes is an R&D project investigating whether one evidence-first, automated approach can close that gap.**

> Can we turn the long tail of the public web into structured, reviewable lists of people and the positions they hold—without writing a custom scraper for every institution?

## Evidence first

Automation makes sources more important, not less. Pages change, captures fail, and model output is probabilistic. An extracted relationship is only useful if we can return to the page as it appeared, inspect it, and apply better extraction methods later.

Funes therefore builds on [Pravda](https://github.com/opensanctions/pravda), which captures and retains rendered web evidence. The evidence remains stable while prompts, models, and pipeline designs evolve. Extracted observations point back to that evidence; an LLM response is never treated as a source in itself.

## What we are trying to learn

The challenge is not whether a model can read one clean leadership page. It is whether an automated system can work reliably, economically, and accountably across the messiness of the real web.

Funes explores questions such as:

- How can one approach handle text, images, organization charts, and linked documents?
- How can it discover which pages are worth monitoring and revisit them cheaply?
- How can it distinguish an empty result from a blocked or incomplete capture?
- How tightly can every extracted name and position be grounded in the captured source?
- How should completeness, correctness, and stability be measured across diverse websites and languages?

Funes exists to turn these questions into repeatable experiments against real pages and frozen evidence.

> [!WARNING]
> Funes is an early-stage research playground, not a production data pipeline. Its scope and architecture are expected to change as the experiments produce answers.
