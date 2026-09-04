"""Both themes have to stay legible, and only one of them was being checked.

The dark theme re-tuned `--udm-navy` from #0b315a to #16222f so the sidebar,
buttons and pills would darken with the page. That is right for navy's
*background* job and fatal for its *text* job: #16222f on the #17212c card
surface is a contrast ratio of 1.01, so headings, stat values, table codes and
donut totals rendered invisible. Every gate stayed green through it — the views
returned 200, the templates balanced, and all 579 tests passed — because nothing
here resolved a custom property to a colour and compared the two.

That is what these do. `--udm-navy-text` now carries the text job separately,
resolving to `--udm-navy` in light and `--udm-ink` in dark.

FIXME: this reads the cascade approximately. It resolves one dark-scoped
override onto the rule it overrides and assumes anything without its own
background sits on the card or the canvas, which is true of this stylesheet but
is not a real specificity model. A rule that is only reachable through a deeper
selector chain would not be caught. Rendering the pages in a headless browser
and sampling computed styles is the real version of this test.
"""

from __future__ import annotations

import pathlib
import re

CSS = pathlib.Path("static/css/doctrack.css")

#: WCAG AA is 4.5:1 for body text and 3:1 for large text. The floor here is 3:1
#: — the point is to catch text that has become unreadable, not to re-tune every
#: shade of grey the design already signed off on.
FLOOR = 3.0

#: Painted on a fixed light ground in both themes on purpose: the sign-in and
#: lockout screens are their own full-bleed composition, the routing slip is a
#: print artefact that happens to be shown on screen, and the printed memo is a
#: document — each pins the light tokens back on itself, so reading them through
#: the dark table would describe a page that does not exist.
PINNED = (".login", ".lockout", ".routing-slip", ".memo-print")

#: Elements whose ground is painted by an ancestor rather than by themselves.
#: Without this the sidebar's white-on-navy text reads as white-on-canvas and
#: the check reports seven failures that are not real.
ANCESTOR_GROUNDS = (
    ((".sidebar", ".app-sidebar", ".nav-item", ".sidebar-office",
      ".sidebar-close", ".sidebar-brand", ".wordmark--light"), "var(--udm-navy)"),
)

#: Light-mode contrast that was already below the floor before the dark-mode
#: work and is left exactly as it was, because the brief for that change was
#: "dont change anything for light mode".
#:
#: FIXME: these are genuine AA failures in light mode — gold #c49a2e carries
#: only 2.44:1 on white, and white-on-gold 2.62:1. Fixing them means re-tuning
#: --udm-gold or giving the gold text a darker ink (--udm-gold-ink is #8a6a12
#: and clears the floor), which is a visible change to the light palette and so
#: wants its own decision. The set must not grow in the meantime.
LIGHT_BASELINE = {
    ".wordmark .udm",
    ".eyebrow",
    ".error-code",
    ".btn-receipt-icon",
    ".uploads-leader-tag",
}

VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*?)\s*)?\)")


def _decomment(css: str) -> str:
    """Drop comments before parsing.

    A selector is everything since the previous brace, so a comma inside a
    comment above a rule splits that rule's selector in the wrong place.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _brace_match(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def _strip_blocks(text: str, opener: str) -> str:
    out, cursor = [], 0
    for m in re.finditer(opener, text):
        if m.start() < cursor:
            continue
        out.append(text[cursor:m.start()])
        cursor = _brace_match(text, m.start()) + 1
    out.append(text[cursor:])
    return "".join(out)


def _token_block(text: str, pattern: str) -> dict[str, str]:
    # re.M matters as much as re.S: these patterns are anchored with ^, and the
    # stylesheet does not begin at ":root". Without it the match fails, the
    # table comes back empty, and every rule silently resolves to None — an
    # audit that passes because it checked nothing.
    m = re.search(pattern + r"\s*\{(.*?)\n\}", text, re.S | re.M)
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1))) if m else {}


def _resolve(value: str | None, table: dict[str, str], depth: int = 0) -> str | None:
    """A declaration's value as a hex colour, following var() indirection."""
    value = (value or "").strip()
    if depth > 8:
        return None
    m = VAR.fullmatch(value)
    if m:
        name, fallback = m.group(1), m.group(2)
        if name in table:
            return _resolve(table[name], table, depth + 1)
        return _resolve(fallback, table, depth + 1) if fallback else None
    return value if re.fullmatch(r"#[0-9a-fA-F]{3,8}", value) else None


def _luminance(hex_colour: str) -> float:
    raw = hex_colour.lstrip("#")[:6]
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _declared(decls: str, prop: str) -> str | None:
    # The lookbehind matters: without it `background` also matches inside
    # `border-color`, and `color` inside `border-left-color`, which are the
    # background job rather than the text job.
    m = re.search(r"(?<![-\w])" + prop + r"(?:-color)?\s*:\s*([^;!]+)", decls)
    return m.group(1) if m else None


def _failures(theme: str) -> list[tuple[str, str, float]]:
    """Rules whose text falls below the floor on the given theme's grounds."""
    css = _decomment(CSS.read_text(encoding="utf-8"))

    table = _token_block(css, r"^:root")
    if theme == "dark":
        table = {**table, **_token_block(css, r':root\[data-theme="dark"\]')}

    surface = _resolve("var(--udm-surface)", table)
    canvas = _resolve("var(--udm-canvas)", table)
    assert surface and canvas, f"{theme}: palette did not resolve, nothing would be checked"

    # Theme-scoped rules override the shared ones, so the comparison has to read
    # the cascade rather than each rule on its own.
    overrides: dict[str, dict[str, str]] = {}
    prefix = ':root[data-theme="dark"] '
    if theme == "dark":
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            for selector in re.sub(r"\s+", " ", m.group(1)).split(","):
                selector = selector.strip()
                if selector.startswith(prefix):
                    entry = overrides.setdefault(selector[len(prefix):].strip(), {})
                    for prop in ("color", "background"):
                        if (value := _declared(m.group(2), prop)) is not None:
                            entry[prop] = value

    # Print is deliberately pinned to the light palette, and the token blocks
    # define the theme rather than consuming it.
    screen = _strip_blocks(css, r"@media print\s*\{")
    screen = _strip_blocks(screen, r':root\[data-theme="dark"\]\s*\{')

    failures, checked = [], 0
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", screen):
        selector = re.sub(r"\s+", " ", m.group(1)).strip()
        decls = m.group(2)
        if not selector or selector.startswith(("@", ":root", "html", "*")):
            continue
        if 'data-theme="dark"' in selector or any(p in selector for p in PINNED):
            continue

        over = overrides.get(selector.split(",")[-1].strip(), {})
        foreground = _resolve(over.get("color") or _declared(decls, "color"), table)
        if not foreground:
            continue

        raw_bg = over.get("background") or _declared(decls, "background")
        own = _resolve(raw_bg, table) if raw_bg else None
        if own:
            grounds = [own]
        else:
            inherited = next(
                (_resolve(ground, table)
                 for prefixes, ground in ANCESTOR_GROUNDS
                 if selector.startswith(prefixes)),
                None,
            )
            grounds = [inherited] if inherited else [surface, canvas]

        checked += 1
        worst = min(_contrast(foreground, g) for g in grounds)
        if worst < FLOOR:
            failures.append((selector[:70], foreground, worst))

    # A floor on the sample: if a refactor moves the palette somewhere this
    # parser cannot follow, the test must fail rather than check nothing and
    # report success. The stylesheet carries ~200 colour-bearing rules.
    assert checked > 150, f"{theme}: only {checked} rules resolved to a colour"
    return failures


def _report(failures):
    return "\n".join(
        f"  {ratio:4.2f}:1  {colour}  {selector}"
        for selector, colour, ratio in sorted(failures, key=lambda f: f[2])
    )


def test_no_dark_mode_text_falls_below_the_contrast_floor():
    """Dark mode is held at zero: this is the theme that broke."""
    assert not _failures("dark"), _report(_failures("dark"))


def test_light_mode_contrast_did_not_get_worse():
    """Light mode is held at its baseline rather than at zero.

    The dark-mode fix was explicitly scoped to leave light alone, and light
    carries five gold-on-white pairings that were already under the floor. They
    are recorded in LIGHT_BASELINE with a FIXME rather than silently passed, so
    the set can shrink but not grow.
    """
    failures = _failures("light")
    selectors = {selector for selector, _, _ in failures}

    new = selectors - LIGHT_BASELINE
    assert not new, "light mode regressed:\n" + _report(
        [f for f in failures if f[0] in new]
    )

    fixed = LIGHT_BASELINE - selectors
    assert not fixed, f"these now pass — drop them from LIGHT_BASELINE: {sorted(fixed)}"


def test_navy_keeps_its_two_jobs_apart():
    """--udm-navy-text is what makes the fix light-safe: it resolves to
    --udm-navy in light, so the ~37 rules that swapped to it render exactly as
    they did, and to --udm-ink in dark, where navy has become a background."""
    css = _decomment(CSS.read_text(encoding="utf-8"))

    light = _token_block(css, r"^:root")
    dark = {**light, **_token_block(css, r':root\[data-theme="dark"\]')}

    assert _resolve("var(--udm-navy-text)", light) == _resolve("var(--udm-navy)", light)
    assert _resolve("var(--udm-navy-text)", dark) == _resolve("var(--udm-ink)", dark)

    # The sign-in screen pins the light palette back; navy-text has to be pinned
    # with it or a dark-theme sign-in gets near-black headings where a
    # light-theme one gets navy.
    pin = _token_block(css, r':root\[data-theme="dark"\] \.login-wrap')
    assert "--udm-navy-text" in pin, "the .login-wrap pin has to carry it too"


def test_the_text_token_never_took_over_navys_background_job():
    """The swap targeted `color:` only.

    `border-left-color: var(--udm-navy)` contains the substring
    `color: var(--udm-navy)`, so a naive search-and-replace also rewrites
    borders, backgrounds and accents. In dark that would drag the structural
    navy up to the light ink and invert the sidebar, the active pills and the
    stat-card rules.
    """
    css = _decomment(CSS.read_text(encoding="utf-8"))

    misused = re.findall(r"([-\w]+)\s*:\s*var\(--udm-navy-text\)", css)
    assert set(misused) <= {"color"}, f"navy-text used for {sorted(set(misused) - {'color'})}"

    # And navy itself must still be doing that background job somewhere.
    assert re.search(r"background(?:-color)?:\s*var\(--udm-navy\)", css)
    assert re.search(r"border(?:-\w+)?-color:\s*var\(--udm-navy\)", css)
