"""Tests for the on-demand DOM outline builder."""

import logging

import pytest

from funes.outline import (
    CandidateLink,
    build_outline,
    candidate_links,
    har_resource_media_types,
)

BASE = "https://example.org/about/"


def test_text_structure() -> None:
    """Headings and paragraphs keep their text; interleaved runs stay in order."""
    html = """
    <html><head><title>Ignored</title><style>p{}</style><script>var x;</script></head>
    <body>
      <h1>  Board   of Directors </h1>
      <p>Simple paragraph.</p>
      <p>Mixed <b>bold</b> and <i>italic</i> content.</p>
    </body></html>
    """
    assert build_outline(BASE, html).splitlines() == [
        '- h1 "Board of Directors"',
        '- p "Simple paragraph."',
        "- p:",
        '  - text: "Mixed"',
        '  - b "bold"',
        '  - text: "and"',
        '  - i "italic"',
        '  - text: "content."',
    ]


def test_linked_and_unlinked_images() -> None:
    """Every img is retained, with src resolved against the page URL."""
    html = """
    <body>
      <a href="/people/jane"><img src="/img/jane.jpg" alt="Jane Doe"></a>
      <img src="https://cdn.example.org/logo.png" alt="">
      <img src="img/group.webp">
      <img alt="missing src">
    </body>
    """
    assert build_outline(BASE, html).splitlines() == [
        "- a [href=https://example.org/people/jane]:",
        "  - img [src=https://example.org/img/jane.jpg] [alt=Jane Doe]",
        "- img [src=https://cdn.example.org/logo.png] [alt=]",
        "- img [src=https://example.org/about/img/group.webp]",
        "- img [alt=missing src]",
    ]


def test_har_body_references() -> None:
    """Images with a stored HAR body are annotated; bodyless entries are not."""
    html = (
        "<body><img src='/img/jane.jpg'><img src='/img/gone.jpg'>"
        "<img src='/img/cached.jpg'></body>"
    )
    har = {
        "log": {
            "entries": [
                {
                    "request": {"url": "https://example.org/img/jane.jpg"},
                    "response": {"content": {"_file": "storage/example.org/aaa.jpg"}},
                },
                {
                    "request": {"url": "https://example.org/img/gone.jpg"},
                    "response": {"content": {"size": -1}},
                },
                {
                    "request": {"url": "https://example.org/img/cached.jpg"},
                    "response": {"content": {"_file": "storage/example.org/bbb.jpg"}},
                },
                {
                    "request": {"url": "https://example.org/img/cached.jpg"},
                    "response": {"content": {"size": -1}},
                },
            ]
        }
    }
    assert build_outline(BASE, html, har).splitlines() == [
        "- img [src=https://example.org/img/jane.jpg] [body=storage/example.org/aaa.jpg]",
        "- img [src=https://example.org/img/gone.jpg]",
        "- img [src=https://example.org/img/cached.jpg] [body=storage/example.org/bbb.jpg]",
    ]


def test_har_body_reference_matches_percent_encoded_request_url() -> None:
    """HTML URL characters are encoded as the browser encodes HAR requests."""
    html = '<body><p>Team</p><img src="/img/team portrait (final).png"></body>'
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "url": "https://example.org/img/team%20portrait%20(final).png"
                    },
                    "response": {"content": {"_file": "storage/example.org/team.png"}},
                }
            ]
        }
    }
    assert build_outline(BASE, html, har).splitlines() == [
        '- p "Team"',
        (
            "- img [src=https://example.org/img/team%20portrait%20(final).png] "
            "[body=storage/example.org/team.png]"
        ),
    ]


def test_empty_subtrees_dropped_and_hidden_retained() -> None:
    """Content-free subtrees vanish; hidden content stays visible to the AI."""
    html = """
    <body>
      <div><span></span></div>
      <div aria-hidden="true"><h1>Decorative</h1></div>
      <section hidden><p>Jane Doe</p></section>
      <button><svg><path d="M0 0"/></svg>Menu</button>
      <ul><li>One</li><li>Two</li></ul>
    </body>
    """
    assert build_outline(BASE, html).splitlines() == [
        '- h1 "Decorative"',
        '- p "Jane Doe"',
        '- button "Menu"',
        "- ul:",
        '  - li "One"',
        '  - li "Two"',
    ]


def test_singular_wrappers_collapse() -> None:
    """A wrapper adding nothing of its own folds into its single child."""
    html = """
    <body>
      <div><div><section><div><img src="/a.png" alt="A"></div></section></div></div>
      <article><p>One</p></article>
      <div>Kept <img src="/b.png"></div>
      <a href="/x"><span>Link text</span></a>
    </body>
    """
    assert build_outline(BASE, html).splitlines() == [
        "- img [src=https://example.org/a.png] [alt=A]",
        '- p "One"',
        '- div "Kept":',
        "  - img [src=https://example.org/b.png]",
        "- a [href=https://example.org/x]:",
        '  - span "Link text"',
    ]


def test_non_http_base_url_fails_loud() -> None:
    with pytest.raises(ValueError):
        build_outline("about/page.html", "<body><p>x</p></body>")


def test_links_resolved_and_deduplicated_in_dom_order() -> None:
    html = """
    <body>
      <a href="/people/jane">Jane</a>
      <a href="bio.html">Bio</a>
      <a href="https://other.example.net/x">Elsewhere</a>
      <a href="/people/jane#top">Jane again</a>
      <a href="mailto:a@b.example">Mail</a>
    </body>
    """
    assert candidate_links(BASE, html) == [
        CandidateLink(url="https://example.org/people/jane", anchor="Jane"),
        CandidateLink(url="https://example.org/about/bio.html", anchor="Bio"),
        CandidateLink(url="https://other.example.net/x", anchor="Elsewhere"),
    ]


def test_links_fragments_stripped_queries_preserved() -> None:
    html = '<body><a href="/search?q=jane&amp;page=2#results">Search</a></body>'
    assert candidate_links(BASE, html) == [
        CandidateLink(url="https://example.org/search?q=jane&page=2", anchor="Search")
    ]


def test_links_anchor_text_normalized() -> None:
    html = """
    <body>
      <a href="/a">  Jane   Doe </a>
      <a href="/b"><img src="/x.png" alt="Ignored"></a>
      <a href="/c">   </a>
    </body>
    """
    assert candidate_links(BASE, html) == [
        CandidateLink(url="https://example.org/a", anchor="Jane Doe"),
        CandidateLink(url="https://example.org/b", anchor=None),
        CandidateLink(url="https://example.org/c", anchor=None),
    ]


def test_links_non_http_schemes_rejected() -> None:
    html = """
    <body>
      <a href="javascript:void(0)">JS</a>
      <a href="ftp://files.example.org/f">FTP</a>
      <a href="tel:+1234">Call</a>
      <a href="/kept">Kept</a>
    </body>
    """
    assert candidate_links(BASE, html) == [
        CandidateLink(url="https://example.org/kept", anchor="Kept")
    ]


def test_links_capped_at_200_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    html = (
        "<body>"
        + "".join(f'<a href="/link/{i}">Link {i}</a>' for i in range(250))
        + "</body>"
    )
    with caplog.at_level(logging.WARNING, logger="funes.outline"):
        links = candidate_links(BASE, html)
    assert len(links) == 200
    assert links[0] == CandidateLink(url="https://example.org/link/0", anchor="Link 0")
    assert any("capped" in record.message for record in caplog.records)


def test_links_non_http_final_url_fails_loud() -> None:
    with pytest.raises(ValueError):
        candidate_links("page.html", "<body><a href='/x'>x</a></body>")


def har_entry(url: str, file: str | None, mime_type: str | None) -> dict:
    """One HAR entry with the given stored body and media type."""
    content: dict = {}
    if file is not None:
        content["_file"] = file
    if mime_type is not None:
        content["mimeType"] = mime_type
    return {"request": {"url": url}, "response": {"content": content}}


def test_media_types_none() -> None:
    assert har_resource_media_types(None) == {}


def test_media_types_bodyless_entries_skipped() -> None:
    har = {
        "log": {
            "entries": [
                har_entry("https://example.org/a", None, "text/html"),
                {
                    "request": {"url": "https://example.org/b"},
                    "response": {"content": {}},
                },
            ]
        }
    }
    assert har_resource_media_types(har) == {}


def test_media_types_normalization() -> None:
    har = {
        "log": {
            "entries": [
                har_entry(
                    "https://example.org/a",
                    "storage/a",
                    "  Image/JPEG ; charset=utf-8 ",
                ),
            ]
        }
    }
    assert har_resource_media_types(har) == {"storage/a": "image/jpeg"}


def test_media_types_multiple_resources() -> None:
    har = {
        "log": {
            "entries": [
                har_entry("https://example.org/a", "storage/a", "text/html"),
                har_entry("https://example.org/b", "storage/b", "image/png"),
                har_entry("https://example.org/c", "storage/c", "application/json"),
            ]
        }
    }
    assert har_resource_media_types(har) == {
        "storage/a": "text/html",
        "storage/b": "image/png",
        "storage/c": "application/json",
    }


@pytest.mark.parametrize("mime_type", [None, "", "  ", "x-unknown", "  X-UNKNOWN  "])
def test_media_types_untyped_bodies_omitted(mime_type: str | None) -> None:
    """Responses Chromium saw no Content-Type for are left out, not guessed."""
    har = {
        "log": {"entries": [har_entry("https://example.org/a", "storage/a", mime_type)]}
    }
    assert har_resource_media_types(har) == {}


def test_media_types_untyped_beacon_among_typed_bodies() -> None:
    """A tracker beacon's untyped body doesn't hide the page's real resources."""
    har = {
        "log": {
            "entries": [
                har_entry(
                    "https://tracker.example.net/pview?u=1", "storage/t", "x-unknown"
                ),
                har_entry("https://example.org/a", "storage/a", "image/png"),
            ]
        }
    }
    assert har_resource_media_types(har) == {"storage/a": "image/png"}


@pytest.mark.parametrize(
    "mime_type",
    [
        "imagejpeg",
        "image/;jpeg",
        "image/png/foo",
        "image / png",
        "image/ png",
    ],
)
def test_media_types_malformed_mime_fails_loud(mime_type: str) -> None:
    har = {
        "log": {"entries": [har_entry("https://example.org/a", "storage/a", mime_type)]}
    }
    with pytest.raises(ValueError, match="malformed mimeType"):
        har_resource_media_types(har)
