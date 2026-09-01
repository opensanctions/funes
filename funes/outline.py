"""Build compact, model-facing outlines from rendered HTML.

The output preserves DOM order while removing empty elements and collapsing
single-child wrappers. Links and images retain resolved resource URLs, and
images may include paths to bodies stored in the snapshot's HAR.

Example::

    - main:
      - h1 "Title"
      - a "Details" [href=https://example.com/details]
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import quote, urldefrag, urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

# Elements whose content never reaches the rendered page.
SKIP_TAGS = frozenset({"head", "script", "style", "noscript", "template"})

# Upper bound on links returned by candidate_links.
MAX_CANDIDATE_LINKS = 200


def _require_http_base(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"outline base URL must be absolute http(s), got {url!r}")


@dataclass(frozen=True)
class CandidateLink:
    """One link worth offering as a next-inspection candidate."""

    url: str
    anchor: str | None


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
    text = _normalize(anchor.get_text(" "))
    return text or None


def build_outline(url: str, html: str, http_archive: dict | None = None) -> str:
    """Render *html*, captured from *url*, as a compact model-facing outline.

    *url* is the page's final URL: the base for resolving ``src``/``href``
    and the exact key matched against HAR request URLs. *http_archive* is the
    snapshot's resolved HAR manifest, or ``None`` when the capture has none.
    """
    _require_http_base(url)
    soup = BeautifulSoup(html, "html.parser")
    bodies = _har_bodies(http_archive)
    root = _build(soup.body if soup.body is not None else soup, url, bodies)
    lines: list[str] = []
    if root is not None:
        if root.text is not None:
            lines.append(f"- text: {_quote(root.text)}")
        for child in root.children:
            if isinstance(child, str):
                lines.append(f"- text: {_quote(child)}")
            else:
                lines.extend(_render(child, 0))
    return "\n".join(lines)


@dataclass
class _Node:
    """One rendered element: its tag, folded leading text, kept attributes."""

    tag: str
    text: str | None = None
    attrs: list[tuple[str, str]] = field(default_factory=list)
    children: list["_Node | str"] = field(default_factory=list)


def _build(tag: Tag, url: str, bodies: dict[str, str]) -> _Node | None:
    """Map one element to a node, or ``None`` when nothing of it is retained."""
    if tag.name in SKIP_TAGS:
        return None

    children: list[_Node | str] = []
    for child in tag.children:
        if isinstance(child, Tag):
            node = _build(child, url, bodies)
            if node is not None:
                children.append(node)
        elif type(child) is NavigableString:
            text = _normalize(str(child))
            if text:
                children.append(text)
    children = _merge_adjacent_text(children)

    node = _Node(tag=tag.name, attrs=_kept_attrs(tag, url, bodies), children=children)
    # A single leading text run becomes the element's name; interleaved runs
    # stay in place as children so document order is preserved.
    if (
        children
        and isinstance(children[0], str)
        and all(not isinstance(child, str) for child in children[1:])
    ):
        node.text = children[0]
        node.children = children[1:]
    # Every <img> is retained; anything else must carry some content.
    if tag.name != "img" and node.text is None and not node.attrs and not node.children:
        return None
    # A wrapper adding nothing of its own folds into its single child, so
    # container chains collapse bottom-up into the element that matters.
    if node.text is None and not node.attrs and len(node.children) == 1:
        child = node.children[0]
        if not isinstance(child, str):
            return child
    return node


def _kept_attrs(tag: Tag, url: str, bodies: dict[str, str]) -> list[tuple[str, str]]:
    """The attributes worth showing: link targets and image sources."""
    attrs: list[tuple[str, str]] = []
    if tag.name == "a":
        href = tag.get("href")
        if isinstance(href, str):
            attrs.append(("href", urljoin(url, href)))
    elif tag.name == "img":
        src = tag.get("src")
        if isinstance(src, str):
            resolved = quote(urljoin(url, src), safe="/:?#[]@!$&'()*+,;=%")
            attrs.append(("src", resolved))
            if resolved in bodies:
                attrs.append(("body", bodies[resolved]))
        alt = tag.get("alt")
        if isinstance(alt, str):
            attrs.append(("alt", alt))
    return attrs


def har_resource_media_types(http_archive: dict | None) -> dict[str, str]:
    """Map each stored body path to its normalized response media type.

    Media types are lowercased and stripped of parameters and whitespace.
    Bodies whose response carried no usable media type are omitted rather
    than guessed at: third-party beacons routinely respond without a
    Content-Type, which Chromium records as a missing or ``x-unknown``
    mimeType. A structurally malformed mimeType still raises: that would
    be a producer bug, not an untyped response.
    """
    media: dict[str, str] = {}
    for url, file, content in _stored_bodies(http_archive):
        media_type = _normalized_media_type(content.get("mimeType"), url)
        if media_type is not None:
            media[file] = media_type
    return media


def _stored_bodies(http_archive: dict | None) -> Iterator[tuple[str, str, dict]]:
    """Yield ``(url, file, content)`` for every HAR entry with a stored body."""
    if http_archive is None:
        return
    for entry in http_archive["log"]["entries"]:
        content = entry["response"]["content"]
        file = content.get("_file")
        if file:
            yield entry["request"]["url"], file, content


def _normalized_media_type(mime_type: str | None, url: str) -> str | None:
    """Normalize a media type; ``None`` when the response type is unknown.

    Missing, blank, and ``x-unknown`` mime types mean Chromium saw no
    Content-Type on the response. Anything else that does not parse as
    ``type/subtype`` raises.
    """
    if mime_type is None or mime_type.strip().lower() in ("", "x-unknown"):
        return None
    media_type = mime_type.split(";", 1)[0].strip().lower()
    parts = media_type.split("/")
    if len(parts) != 2 or not all(part.split() == [part] for part in parts):
        raise ValueError(
            f"stored body for {url!r} has malformed mimeType {mime_type!r}"
        )
    return media_type


def _har_bodies(http_archive: dict | None) -> dict[str, str]:
    """Map HAR request URLs to their stored body paths (``content._file``).

    The latest entry with a stored body wins; bodyless entries never shadow
    an earlier body for the same URL.
    """
    bodies: dict[str, str] = {}
    for url, file, _content in _stored_bodies(http_archive):
        bodies[url] = file
    return bodies


def _render(node: _Node, depth: int) -> list[str]:
    """Emit one element's line, then its children one level deeper."""
    pad = "  " * depth
    key = node.tag
    if node.text is not None:
        key += " " + _quote(node.text)
    for name, value in node.attrs:
        key += f" [{name}={value}]"
    line = f"{pad}- {key}"
    if not node.children:
        return [line]
    lines = [line + ":"]
    for child in node.children:
        if isinstance(child, str):
            lines.append(f"{'  ' * (depth + 1)}- text: {_quote(child)}")
        else:
            lines.extend(_render(child, depth + 1))
    return lines


def _merge_adjacent_text(children: list["_Node | str"]) -> list["_Node | str"]:
    """Join neighbouring text runs dropped-element gaps leave behind."""
    merged: list[_Node | str] = []
    for child in children:
        if isinstance(child, str) and merged and isinstance(merged[-1], str):
            merged[-1] = f"{merged[-1]} {child}"
        else:
            merged.append(child)
    return merged


def _normalize(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip the ends."""
    return " ".join(text.split())


def _quote(text: str) -> str:
    """Quote a text value as a JSON string, keeping non-ASCII readable."""
    return json.dumps(text, ensure_ascii=False)
