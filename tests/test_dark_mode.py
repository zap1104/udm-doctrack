"""Dark mode is the light theme's tokens given a second set of values.

That is the whole design, and it holds only while every colour on the page is a
token and every token has both values. The overhaul that produced this file
found it had come apart in ways no single page showed: components painting
with the light theme's hex literals and a dark "repair" rule behind each one,
Bootstrap still drawing its own link blue and pale alerts because nothing told
it the page was dark, office badges as pale chips on a dark table, print
getting the lightened brand hues on white paper, and a flash of the light
theme on every page load for anyone reading in dark.

test_theme_contrast.py checks that the text is legible. These check the
plumbing that makes it so, so the next component added cannot quietly opt out.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from django.contrib import messages
from django.contrib.messages.storage.base import Message

from tests.test_theme_contrast import DARK, LIGHT, _contrast, _decomment, _resolve, _token_block

CSS = pathlib.Path("static/css/doctrack.css")
TEMPLATES = pathlib.Path("templates")
THEME_INIT = pathlib.Path("static/js/theme-init.js")
APP_JS = pathlib.Path("static/js/doctrack.js")

#: Tokens that are the same in both themes because they are not colours.
#: --bs-link-* are colours, but the dark theme sets them on body alongside the
#: rest of Bootstrap's variables rather than in its token block.
THEME_NEUTRAL = {
    "--sidebar-width", "--radius",
    "--bs-link-color", "--bs-link-color-rgb", "--bs-link-hover-color", "--bs-link-hover-color-rgb",
}

#: The three things that are paper whatever the screen is.
PAPER = (".login-wrap", ".memo-print", ".routing-slip")


def _css() -> str:
    return _decomment(CSS.read_text(encoding="utf-8"))


def _themes() -> dict[str, dict[str, str]]:
    """Each theme's resolved token table: light, dark, and dark sent to print."""
    css = _css()
    light = _token_block(css, LIGHT)
    dark = {**light, **_token_block(css, DARK)}
    printed = {**dark, **_print_block(css)}
    return {"light": light, "dark": dark, "print": printed}


def _print_block(css: str) -> dict[str, str]:
    m = re.search(r"@media print\s*\{\s*" + DARK + r"\s*\{(.*?)\}", css, re.S)
    assert m, "the print reset of the dark palette is gone"
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1)))


def _rules(css: str) -> list[tuple[str, str]]:
    return [(re.sub(r"\s+", " ", s).strip(), body) for s, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


# --- the palette ---------------------------------------------------------------
def test_every_colour_token_has_a_dark_value():
    """A token the dark block does not re-point keeps its light value on the dark
    page: a pale panel, or a dark ink on the dark card. It is how a component
    added after the dark theme gets missed, so a new token has to say either
    what it is in dark or, here, why it does not change."""
    css = _css()
    light = _token_block(css, LIGHT)
    dark = _token_block(css, DARK)

    missing = sorted(set(light) - set(dark) - THEME_NEUTRAL)
    assert not missing, f"no dark value for: {missing}"


def test_every_token_a_rule_uses_is_defined():
    """var(--udm-typo) with no fallback is not an error to the browser: the
    declaration silently falls back to inherited or initial, which is usually
    black text or no background, and usually only in one theme."""
    css = _css()
    defined = set(re.findall(r"(--[\w-]+)\s*:", css))
    sources = [CSS, *sorted(TEMPLATES.rglob("*.html"))]
    used = {
        (path.as_posix(), name)
        for path in sources
        for name in re.findall(r"var\(\s*(--(?:udm|chart|status|shadow)-[\w-]+)\s*\)", path.read_text(encoding="utf-8"))
    }

    undefined = sorted(f"{path}: {name}" for path, name in used if name not in defined)
    assert not undefined, "\n".join(undefined)


def test_printing_from_dark_prints_the_light_palette():
    """Print resets the dark tokens to the light ones. A token the dark block
    re-points and the print block forgets goes to paper in its dark value — the
    lightened gold on white is 1.9:1. Sixteen of them had been forgotten."""
    css = _css()
    light = _token_block(css, LIGHT)
    dark = _token_block(css, DARK)
    printed = _print_block(css)

    missing = sorted(set(dark) - set(printed))
    assert not missing, f"the print block does not reset: {missing}"

    # And resets them to what light mode prints, not to something of its own.
    # The page itself is the exception: paper is white rather than the canvas grey.
    table = {**light, **dark, **printed}
    drifted = sorted(
        name for name in printed
        if name != "--udm-canvas"
        and (_resolve(printed[name], table) or printed[name].strip()).lower()
        != (_resolve(light[name], light) or light[name].strip()).lower()
    )
    assert not drifted, f"print differs from light for: {drifted}"
    assert _resolve(printed["--udm-canvas"], table) == "#ffffff"
    assert "color-scheme: light" in re.search(r"@media print\s*\{\s*" + DARK + r"\s*\{(.*?)\}", css, re.S).group(1)


def test_dark_office_badges_stay_on_screen():
    """A badge's dark pair is inline on the badge, not a token, so the print
    reset cannot reach it: printed from dark, the paper kept the lightened ink
    and dropped the wash under it, and office codes came out pale on white."""
    css = _css()
    rule = re.search(r'(@media screen\s*\{\s*)?:root\[data-theme="dark"\] \.office-badge\s*\{', css)

    assert rule, "the dark theme's badge rule is gone"
    assert rule.group(1), "the dark theme's badge rule has to be screen-only"


#: (text, ground) pairs that the role tokens promise, checked in each theme at
#: WCAG AA for body text. The light values are what those rules painted before
#: they were tokens; the dark ones are the reason the tokens exist.
ROLE_PAIRS = [
    ("--udm-primary-ink", "--udm-primary"),
    ("--udm-primary-ink", "--udm-primary-hover"),
    ("--udm-selected-ink", "--udm-selected-bg"),
    ("--udm-selected-ink", "--udm-selected-bg-hover"),
    ("--udm-on-accent", "--udm-red"),
    ("--udm-on-accent", "--udm-red-600"),
    ("--udm-on-accent", "--udm-teal"),
    ("--udm-on-accent", "--udm-green"),
    ("--udm-on-gold", "--udm-gold"),
    ("--udm-note-ink", "--udm-note-bg"),
    ("--udm-slate-ink", "--udm-track"),
    ("--udm-ink", "--udm-highlight"),
    ("--udm-slate-text", "--udm-surface-sunk"),
    ("--udm-gold-ink", "--udm-canvas"),
    ("--udm-gold-ink", "--udm-surface"),
    ("--udm-teal", "--udm-surface"),
    ("--udm-navy-text", "--udm-surface"),
]


@pytest.mark.parametrize("theme", ["light", "dark", "print"])
@pytest.mark.parametrize(("text", "ground"), ROLE_PAIRS, ids=[f"{t}-on-{g}" for t, g in ROLE_PAIRS])
def test_role_tokens_clear_aa_in_every_theme(theme, text, ground):
    table = _themes()[theme]
    fg, bg = _resolve(f"var({text})", table), _resolve(f"var({ground})", table)
    assert fg and bg, f"{theme}: {text} or {ground} did not resolve to a colour"

    assert _contrast(fg, bg) >= 4.5, f"{theme}: {text} {fg} on {ground} {bg} is {_contrast(fg, bg):.2f}:1"


def test_bootstraps_link_rgb_matches_the_teal_it_stands_for():
    """Bootstrap builds its link colour from an "r, g, b" triple, so the teal is
    written twice, once as hex and once as the triple, in each theme. Re-tune
    one and not the other and links go back to a colour nobody chose."""
    css = _css()
    themes = _themes()
    dark_body = next(body for selector, body in _rules(css) if selector == ':root[data-theme="dark"] body')
    blocks = {"light": _token_block(css, LIGHT), "dark": dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", dark_body))}

    for theme, block in blocks.items():
        for triple, token in (("--bs-link-color-rgb", "--udm-teal"), ("--bs-link-hover-color-rgb", "--udm-teal-ink")):
            hex_value = _resolve(f"var({token})", themes[theme]).lstrip("#")
            expected = ", ".join(str(int(hex_value[i:i + 2], 16)) for i in (0, 2, 4))
            assert block[triple].strip() == expected, f"{theme}: {triple} is {block[triple]}, {token} is {expected}"


# --- the rules -------------------------------------------------------------------
def test_white_text_is_only_ever_on_a_ground_that_stays_dark():
    """`color: #fff` is right on the sidebar, which is navy in both themes, and
    on the sign-in artwork. Anywhere else the ground is a token that lightens in
    the dark theme — white on the dark theme's teal is 2.7:1 — so text on a
    solid fill takes the role that fill promises instead."""
    stays_dark = (".nav-item", ".sidebar", ".app-sidebar", ".wordmark--light", ".login-")
    css = _strip_print_and_tokens(_css())

    offenders = [
        selector for selector, body in _rules(css)
        if re.search(r"(?<![-\w])color\s*:\s*(?:#fff\b|#ffffff\b|white\b)", body)
        and not all(part.strip().startswith(stays_dark) for part in selector.split(","))
    ]
    assert not offenders, f"white text on a themed ground: {offenders}"


def _strip_print_and_tokens(css: str) -> str:
    css = re.sub(r"@media print\s*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)
    return re.sub(LIGHT + r"\{[^{}]*\}", "", css, flags=re.M)


def test_selected_controls_paint_with_the_selected_role():
    """A queue pill, a notification filter, a page size and a report tab are the
    same state and read as one in both themes, hovered or not. Each carried its
    own navy and white, with a dark-theme repair rule behind some of them and
    not others. A control back on its own navy and white is dark navy on the
    dark card, and under the pointer it stops matching the rest."""
    css = _css()
    rules = dict(_rules(css))
    selected = {
        ".pill-toggle.is-active": ".pill-toggle.is-active:hover,.pill-toggle.is-active:focus",
        ".notification-filter.is-active": ".notification-filter.is-active:hover,.notification-filter.is-active:focus-visible",
        ".page-size-option.is-active": ".page-size-option.is-active:hover",
        ".reports-tab.active": ".reports-tab.active:hover,.reports-tab.active:focus",
    }
    for plain, hovered in selected.items():
        for selector in (plain, hovered):
            body = rules.get(selector)
            assert body is not None, f"no rule for {selector}"
            assert re.search(r"background\s*:\s*var\(--udm-selected-bg(?:-hover)?\)", body), selector
            assert re.search(r"(?<![-\w])color\s*:\s*var\(--udm-selected-ink\)", body), selector


def test_no_rule_for_a_select_uses_the_background_shorthand():
    """`background:` resets background-image too, and background-image is where
    Bootstrap draws a select's arrow. The dark theme's rule for form controls
    used the shorthand, and every dropdown on a dark page lost its arrow."""
    offenders = [
        selector for selector, body in _rules(_css())
        if ".form-select" in selector and re.search(r"(?<![-\w])background\s*:", body)
    ]
    assert not offenders, offenders


@pytest.mark.parametrize("level", [messages.INFO, messages.SUCCESS, messages.WARNING, messages.ERROR])
def test_every_message_level_has_a_styled_banner(level):
    """The banner's class is `alert-<tag>`, and Django tags an error `error`,
    which is not one of Bootstrap's colours: error banners rendered with no
    colour at all in either theme. The CSS has to know every tag Django sends."""
    bootstrap = {"primary", "secondary", "success", "danger", "warning", "info", "light", "dark"}
    tag = Message(level, "probe").tags

    assert tag in bootstrap or re.search(rf"\.alert-{tag}\s*\{{", _css()), f".alert-{tag} is not styled"


# --- the page --------------------------------------------------------------------
def test_theme_init_sets_both_attributes_from_the_same_preference():
    script = THEME_INIT.read_text(encoding="utf-8")
    app = APP_JS.read_text(encoding="utf-8")

    key = re.search(r'STORAGE_KEY\s*=\s*"([^"]+)"', app).group(1)
    assert f'"{key}"' in script, "the first paint and the toggle must read the same preference"
    for attribute in ("data-theme", "data-bs-theme"):
        assert f'setAttribute("{attribute}"' in script, f"theme-init.js does not set {attribute}"
        assert f'setAttribute("{attribute}"' in app, f"doctrack.js does not keep {attribute} in step"


@pytest.mark.django_db
def test_the_theme_is_applied_before_the_stylesheets(client):
    """Loaded at the end of <body>, the theme arrived after the first paint, so
    every page opened light and then turned dark. It has to be a plain external
    script, ahead of the first stylesheet: the CSP allows no inline script, and
    async or defer would put it back after the paint."""
    body = client.get("/accounts/login/").content.decode()
    head = body[:body.index("</head>")]

    tag = re.search(r"<script[^>]*theme-init\.js[^>]*>", head)
    assert tag, "theme-init.js is not in <head>"
    assert not re.search(r"\b(?:async|defer)\b", tag.group(0))
    assert tag.start() < head.index('rel="stylesheet"')
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", head), "inline script in <head> would be blocked"


def test_paper_takes_the_light_palette_and_tells_bootstrap():
    """The sign-in screen, the printed memo and the routing slip stay light in
    the dark theme. Two things make that true, and a surface with one and not
    the other is half dark: the app's tokens come from the light palette's own
    selector list, and Bootstrap's from data-bs-theme="light" on the element."""
    css = _css()
    selector = re.search(LIGHT + r"\{", css, re.M).group(0)
    tags = [
        (path, tag)
        for path in TEMPLATES.rglob("*.html")
        for tag in re.findall(
            r'<[a-z]+[^>]*\bclass="[^"]*(?<![-\w])(?:login-wrap|memo-print|routing-slip)(?![-\w])[^"]*"[^>]*>',
            path.read_text(encoding="utf-8"),
        )
    ]

    for paper in PAPER:
        assert paper in selector, f"{paper} is not in the light palette's selector list"
        named = re.compile(r'class="[^"]*(?<![-\w])' + paper[1:] + r"(?![-\w])")
        assert any(named.search(tag) for _, tag in tags), f"no template renders {paper}"
    unmarked = [f"{path}: {tag}" for path, tag in tags if 'data-bs-theme="light"' not in tag]
    assert not unmarked, "\n".join(unmarked)
