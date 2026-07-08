"""Server-side HTML sanitization for stored rich text (Foundation contract).

Notice bodies (and any future rich-text field) are stored **already sanitized**
so the value read back is safe to render — no client-side trust required
(docs/modules/notice-board.md §4/§7 lists an HTML sanitizer as a Foundation
utility). This is the ONE place the whitelist policy lives, so every rich-text
writer strips the same way and stored XSS (``<script>``, ``on*`` handlers,
``javascript:``/``data:`` URLs) cannot land.

Backed by ``nh3`` — the Rust ``ammonia`` binding: actively maintained,
memory-safe, and far faster than the deprecated ``bleach`` (vetted 2026-07-08,
see requirements.txt). ``nh3.clean`` drops disallowed tags/attributes and unsafe
URL schemes by construction; we pass an explicit allow-list so the policy is
visible and testable rather than relying on the library defaults.
"""
from __future__ import annotations

import nh3

# The allow-list the notice body (and any future rich text) is held to. Kept here
# so the policy is defined once; callers pass these through ``sanitize_html``.
#
# Formatting only — no ``img``/``iframe``/``style``/``script``, no ``class``/``id``/
# ``style`` attributes, no event handlers. Links may carry ``href``/``title`` and
# are restricted to safe schemes (``javascript:``/``data:`` are dropped).
ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "p",
        "br",
        "span",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "s",
        "ul",
        "ol",
        "li",
        "a",
        "h1",
        "h2",
        "h3",
        "h4",
        "blockquote",
        "code",
        "pre",
        "hr",
    }
)

ALLOWED_ATTRIBUTES: dict[str, set[str]] = {"a": {"href", "title"}}

ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})


def sanitize_html(raw: str) -> str:
    """Return ``raw`` stripped to the safe-to-store/render allow-list above.

    Drops any tag/attribute not on the list, every event-handler attribute, and
    any ``href`` whose scheme is not in :data:`ALLOWED_URL_SCHEMES` (so
    ``javascript:``/``data:`` URLs cannot survive). Idempotent: sanitizing an
    already-clean value is a no-op. The result is safe to persist and render
    without further escaping.
    """
    return nh3.clean(
        raw,
        tags=set(ALLOWED_TAGS),
        attributes={tag: set(attrs) for tag, attrs in ALLOWED_ATTRIBUTES.items()},
        url_schemes=set(ALLOWED_URL_SCHEMES),
        link_rel="noopener noreferrer",
        strip_comments=True,
    )


def sanitize_plain_text(raw: str) -> str:
    """Strip ALL HTML from ``raw``, leaving only its text content.

    For plain-text fields (e.g. a notice title) that must never carry markup:
    ``nh3.clean`` with an empty tag allow-list removes every tag (and the
    contents of ``script``/``style``) while keeping the visible text, so a
    ``<script>`` or ``<b>`` in the input is neutralized rather than stored
    verbatim. Defense-in-depth for any context that later renders the value into
    HTML/email/a notification.
    """
    return nh3.clean(raw, tags=set(), attributes={}, strip_comments=True)
