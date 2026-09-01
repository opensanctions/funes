"""Follow-up link selection and the Pydantic AI discovery agent.

Discovery is its own pipeline stage: the discovery agent reads the same page
snapshot prompt the extraction agent judged — diagnostics, metadata, and the
full outline — and selects the links worth inspecting next against the
trusted brief. Every selected link becomes an inspection job of its own.
"""

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup
from pydantic import Field
from pydantic_ai import Agent, NativeOutput, RunContext

from funes.agents import Brief, NonBlank, StrictModel, render_brief

_DISCOVERY_INSTRUCTIONS = """\
# Identity

You select follow-up links from one captured web page against a trusted
runtime brief: the people sought name the class of position holders, and the
labeled subject scopes the search to one organization, country, court, or
other entity. Every selected link becomes an inspection job of its own.

# Selection policy

- Links are the a elements of the page outline: select only URLs written
  there as [href=...], copying each exactly. Never construct, modify, or
  guess a URL, and never select an image [src=...] or [body=...]
  reference.
- Judge each link by its anchor text together with its context in the
  outline: the heading it sits under, adjacent text, and the enclosing
  section often say where a generic anchor such as "Read more" or
  "Details" leads.
- Select links likely to lead to pages naming people covered by the brief:
  rosters and membership lists, individual biographies and profiles, team
  and leadership pages, former-officeholder pages, and organization charts.
- Skip navigation, footer, login, print, social-media, and
  language-switcher links, and links clearly unrelated to the subject.
- Be inclusive of plausible links: selected links become real inspection
  jobs, so select a link when it could plausibly lead to brief-covered people.
- Select each distinct URL once, even when the page links to it repeatedly.
- Give a one-sentence reason for each selection naming what the link
  appears to lead to.
"""


def _require_http_base(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"link enumeration base must be absolute http(s), got {url!r}")


def page_link_urls(final_url: str, html: str) -> set[str]:
    """The set of http(s) link targets in *html*, resolved against *final_url*.

    Fragments are stripped; query parameters are kept. This is the
    validation universe for discovery selections: every href the page
    outline shows the model is in this set.
    """
    _require_http_base(final_url)
    soup = BeautifulSoup(html, "html.parser")
    urls: set[str] = set()
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        url, _fragment = urldefrag(urljoin(final_url, href))
        if url.startswith(("http://", "https://")):
            urls.add(url)
    return urls


class LinkSelection(StrictModel):
    """One candidate URL chosen for a follow-up inspection job."""

    url: NonBlank = Field(
        description="Candidate URL copied exactly as written in the page outline.",
    )
    reason: NonBlank = Field(
        description=(
            "One sentence naming what this link appears to lead to and why it "
            "may name brief-covered people."
        ),
    )


class Discovery(StrictModel):
    """The links selected from one captured page."""

    selections: list[LinkSelection] = Field(
        description="Every link worth inspecting, with its reason.",
    )


# The discovery agent picks follow-up links from a captured page against the
# same brief. It runs in a fresh context with no tools: its user message is
# the same page snapshot prompt the extraction agent judged.
discovery_agent: Agent[Brief, Discovery] = Agent(
    name="discovery",
    output_type=NativeOutput(Discovery, strict=True),
    instructions=_DISCOVERY_INSTRUCTIONS,
    model_settings={"thinking": "low"},
    deps_type=Brief,
)


@discovery_agent.instructions
def _discovery_brief(ctx: RunContext[Brief]) -> str:
    """Render the run's trusted selection brief as dynamic instructions."""
    return render_brief(ctx.deps)
