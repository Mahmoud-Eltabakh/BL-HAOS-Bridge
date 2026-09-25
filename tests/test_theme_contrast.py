"""Contrast guards for the dashboard palette in ``web_ui/src/index.css``.

An earlier revision of the neumorphic theme removed every border and kept the
three surface tones within ~1.08:1 of each other, so cards, wells and buttons
all rendered as one flat grey mass. These checks keep the surface ramp and the
text tiers honest, following the same source-inspection style as
``test_frontend_assets.py``.
"""

import re
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
INDEX_CSS = MODULE_ROOT / "web_ui" / "src" / "index.css"

# WCAG 2.1 minimums.
AA_NORMAL_TEXT = 4.5
AA_NON_TEXT = 3.0


def _read_css() -> str:
    assert INDEX_CSS.exists(), f"{INDEX_CSS} must exist"
    return INDEX_CSS.read_text(encoding="utf-8")


def _tokens() -> dict:
    block = re.search(r":root\s*\{([\s\S]*?)\}", _read_css())
    assert block, "index.css must declare its palette inside a :root block"
    return {
        name: value.strip()
        for name, value in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", block.group(1))
    }


def _parse_color(value: str) -> tuple:
    """Return ``(r, g, b, alpha)`` for a hex or rgb(a) colour."""
    rgba = re.match(
        r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+))?\s*\)", value
    )
    if rgba:
        r, g, b, a = rgba.groups()
        return (float(r), float(g), float(b), 1.0 if a is None else float(a))

    hex_value = value.strip().lstrip("#")
    if len(hex_value) == 3:
        hex_value = "".join(char * 2 for char in hex_value)
    assert len(hex_value) == 6, f"Unsupported colour literal: {value}"
    return (
        float(int(hex_value[0:2], 16)),
        float(int(hex_value[2:4], 16)),
        float(int(hex_value[4:6], 16)),
        1.0,
    )


def _blend(over: str, under: str) -> tuple:
    """Composite a translucent colour over an opaque backdrop."""
    r, g, b, alpha = _parse_color(over)
    br, bg, bb, _ = _parse_color(under)
    return (
        round(r * alpha + br * (1 - alpha)),
        round(g * alpha + bg * (1 - alpha)),
        round(b * alpha + bb * (1 - alpha)),
    )


def _relative_luminance(rgb: tuple) -> float:
    def channel(value: float) -> float:
        scaled = value / 255
        return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4

    r, g, b = rgb[:3]
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(foreground: tuple, background: tuple) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _rgb(token_name: str, tokens: dict) -> tuple:
    assert token_name in tokens, f"index.css must define --{token_name}"
    return _parse_color(tokens[token_name])[:3]


SURFACE_TOKENS = {
    "page": "neu-page",
    "card": "neu-surface",
    "well": "neu-surface-deep",
    "raised": "neu-surface-raised",
}
TEXT_TOKENS = {
    "primary": "neu-text",
    "muted": "neu-muted",
    "faint": "neu-faint",
}


def test_surface_tiers_are_visibly_separated():
    """Panels must read as panels even with the soft shadows flattened."""
    tokens = _tokens()
    page = _rgb(SURFACE_TOKENS["page"], tokens)
    card = _rgb(SURFACE_TOKENS["card"], tokens)
    well = _rgb(SURFACE_TOKENS["well"], tokens)
    raised = _rgb(SURFACE_TOKENS["raised"], tokens)

    # The regression this guards against measured 1.08:1 between card and page.
    assert _contrast(card, page) >= 1.2, "card surface must separate from the page"
    assert _contrast(well, card) >= 1.25, "wells/inputs must separate from cards"
    assert _contrast(raised, card) >= 1.15, "raised buttons must separate from cards"


def test_text_tiers_meet_wcag_aa_on_every_surface():
    tokens = _tokens()
    surfaces = {name: _rgb(token, tokens) for name, token in SURFACE_TOKENS.items()}
    for tier, token in TEXT_TOKENS.items():
        for surface_name, surface in surfaces.items():
            ratio = _contrast(_rgb(token, tokens), surface)
            assert ratio >= AA_NORMAL_TEXT, (
                f"--{token} ({tier} text) on the {surface_name} surface is "
                f"{ratio:.2f}:1, below the {AA_NORMAL_TEXT}:1 AA requirement"
            )


def test_borders_are_present_on_every_surface_class():
    """Borderless soft surfaces were the core of the low-contrast regression."""
    css = _read_css()
    for selector in (".neu-surface", ".neu-inset", ".neu-control", ".neu-button"):
        block = re.search(rf"\{selector}\s*\{{([\s\S]*?)\}}", css)
        assert block, f"{selector} must be defined in index.css"
        assert re.search(r"border:\s*1px solid", block.group(1)), (
            f"{selector} must keep a 1px solid border so its edge is visible"
        )


def test_border_tokens_stay_visible_against_cards():
    tokens = _tokens()
    card = _rgb(SURFACE_TOKENS["card"], tokens)
    soft = _parse_color(tokens["neu-border"])
    strong = _parse_color(tokens["neu-border-strong"])

    assert soft[3] >= 0.15, "--neu-border must stay perceptible"
    assert strong[3] >= 0.4, "--neu-border-strong must stay clearly visible"
    assert _contrast(_blend(tokens["neu-border"], tokens["neu-surface"]), card) >= 1.3
    assert _contrast(_blend(tokens["neu-border-strong"], tokens["neu-surface"]), card) >= 2.0


def test_focus_ring_meets_non_text_contrast():
    css = _read_css()
    tokens = _tokens()
    ring = re.search(r":focus-visible\s*\{[^}]*outline:\s*2px solid ([^;]+);", css)
    assert ring, ":focus-visible must declare an outline so keyboard focus is visible"

    ring_color = _parse_color(ring.group(1).strip())[:3]
    for surface in SURFACE_TOKENS:
        ratio = _contrast(ring_color, _rgb(SURFACE_TOKENS[surface], tokens))
        assert ratio >= AA_NON_TEXT, f"focus ring on the {surface} surface is only {ratio:.2f}:1"
