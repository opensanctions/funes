"""Tests for the on-demand DOM outline builder."""

import pytest

from funes.outline import build_outline

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
