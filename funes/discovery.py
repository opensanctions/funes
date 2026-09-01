"""Follow-up link enumeration and the Pydantic AI discovery agent.

Discovery is its own pipeline stage: `candidate_links` enumerates the
http(s) links of a captured page, and the discovery agent selects the ones
worth inspecting next against the trusted brief. Every selected link
becomes an inspection job of its own.
"""

import json
import logging
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup, Tag
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

- Select only from the enumerated candidate links, copying each URL exactly
  as written. Never construct, modify, or guess a URL.
- Select links likely to lead to pages naming people covered by the brief:
  rosters and membership lists, individual biographies and profiles, team
  and leadership pages, former-officeholder pages, and organization charts.
- Skip navigation, footer, login, print, social-media, and
  language-switcher links, and links clearly unrelated to the subject.
- Be inclusive of plausible links: selected links become real inspection
  jobs, so select a link when it could plausibly lead to brief-covered people.
- Give a one-sentence reason for each selection naming what the link
  appears to lead to.
"""

# Upper bound on links returned by candidate_links.
MAX_CANDIDATE_LINKS = 200


@dataclass(frozen=True)
class CandidateLink:
    """One link worth offering as a next-inspection candidate."""

    url: str
    anchor: str | None


def _require_http_base(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"link enumeration base must be absolute http(s), got {url!r}")


def candidate_links(final_url: str, html: str) -> list[CandidateLink]:
    """Extract http(s) links from *html*, resolved against *final_url*.

    Fragments are stripped; query parameters are kept. Links deduplicate by
    URL in DOM order, and at most :data:`MAX_CANDIDATE_LINKS` are returned.
    """
    _require_http_base(final_url)
    soup = BeautifulSoup(html, "html.parser")
    links: list[CandidateLink] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        url, _fragment = urldefrag(urljoin(final_url, href))
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        links.append(CandidateLink(url=url, anchor=_anchor_text(anchor)))
    if len(links) > MAX_CANDIDATE_LINKS:
        logging.getLogger(__name__).warning(
            "candidate links capped at %d of %d for %s",
            MAX_CANDIDATE_LINKS,
            len(links),
            final_url,
        )
    return links[:MAX_CANDIDATE_LINKS]


def _anchor_text(anchor: Tag) -> str | None:
    """Whitespace-collapsed anchor text, or ``None`` when blank."""
    text = " ".join(anchor.get_text(" ").split())
    return text or None


class LinkSelection(StrictModel):
    """One candidate URL chosen for a follow-up inspection job."""

    url: NonBlank = Field(
        description="Candidate URL copied exactly as enumerated.",
    )
    reason: NonBlank = Field(
        description=(
            "One sentence naming what this link appears to lead to and why it "
            "may name brief-covered people."
        ),
    )


class Discovery(StrictModel):
    """The links selected from one page's candidate links."""

    selections: list[LinkSelection] = Field(
        description="Every candidate link worth inspecting, with its reason.",
    )


# The discovery agent picks follow-up links from a captured page against the
# same brief. It runs in a fresh context with no tools: the prompt carries
# only the enumerated candidate links.
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


def build_discovery_prompt(links: list[CandidateLink]) -> str:
    """Build one user message enumerating the page's candidate links.

    The prompt carries the untrusted link list only; the trusted brief
    travels separately as run instructions.
    """
    entries = "".join(
        f"{index}. URL: {link.url}\n   Anchor text: {_quote_anchor(link.anchor)}\n"
        for index, link in enumerate(links, start=1)
    )
    return f"<candidate_links>\n{entries}</candidate_links>"


def _quote_anchor(text: str | None) -> str:
    """Quote anchor text as a JSON string, keeping non-ASCII readable.

    A link with no anchor text renders as JSON ``null``.
    """
    if text is None:
        return "null"
    return json.dumps(text, ensure_ascii=False)
