"""Appearance themes for the whole app, in one central place.

Every colour the app uses comes from CSS variables ("design tokens") created by this
file. static/style.css and the templates only ever say var(--something), so changing a
colour here changes it everywhere.

To add or change a theme, edit THEMES below. Each theme has a light and a dark variant
of 8 colours; everything else (button text, links, focus ring, chart lines, header)
is worked out from them so text always stays readable.

Run  python -m app.themes  to test the contrast of every theme and mode.
"""

import re
from typing import Dict, List, Optional

APP_NAME = "Health App"
DEFAULT_THEME = "eevee"
DEFAULT_MODE = "light"
MODES = ["light", "dark", "system"]
HEX_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def palette(bg, surface, surface_alt, border, text, muted, primary, accent) -> Dict[str, str]:
    return {
        "bg": bg,  # page background
        "surface": surface,  # cards
        "surface_alt": surface_alt,  # tinted areas
        "border": border,
        "text": text,
        "muted": muted,  # secondary text
        "primary": primary,  # header, buttons
        "accent": accent,  # highlights, focus ring
    }


THEMES = {
    "eevee": {
        "name": "Eevee",
        "tagline": "Warm brown and cream",
        "light": palette("#f6efe6", "#fffaf3", "#efe4d4", "#dccdb8", "#3b2a1c", "#6b5844", "#8a5a2b", "#c28a4a"),
        "dark": palette("#1e1710", "#2a2118", "#352a1f", "#4a3b2b", "#f3e8da", "#c7b59d", "#d4a373", "#f0c27b"),
    },
    "vaporeon": {
        "name": "Vaporeon",
        "tagline": "Aqua and ocean blue",
        "light": palette("#eaf6fb", "#ffffff", "#d9edf6", "#b9d9e8", "#0d2b3e", "#3d6074", "#0b6e99", "#19b5c7"),
        "dark": palette("#0a1a24", "#102a38", "#163748", "#24506a", "#e6f6fb", "#a9cfdf", "#4fc3e8", "#7ee8e0"),
    },
    "jolteon": {
        "name": "Jolteon",
        "tagline": "Yellow, muted gold and olive",
        "light": palette("#faf6e0", "#fffdf0", "#f0e9bf", "#dad29a", "#33300f", "#625c24", "#7d6b0a", "#e6c200"),
        "dark": palette("#1a1800", "#26230a", "#322f12", "#4d481c", "#f8f3c8", "#cfc88a", "#e6c92f", "#b5b84a"),
    },
    "flareon": {
        "name": "Flareon",
        "tagline": "Warm orange, amber and cream",
        "light": palette("#fdf1e3", "#fffaf3", "#fbe3c8", "#efc9a0", "#3d1d0b", "#7a4a2a", "#b8470f", "#f2a33a"),
        "dark": palette("#1f0f06", "#2e1810", "#3b2016", "#5a3320", "#fdebd8", "#e0b999", "#ff9a4d", "#ffc766"),
    },
    "espeon": {
        "name": "Espeon",
        "tagline": "Purple and lilac",
        "light": palette("#f3effa", "#ffffff", "#e6def5", "#cdbfe6", "#25183d", "#5b4a82", "#6b3fb0", "#b78bf0"),
        "dark": palette("#150e24", "#21173a", "#2c2050", "#443373", "#efe9fc", "#bfb0e0", "#b99bf2", "#e0a6f0"),
    },
    "umbreon": {
        "name": "Umbreon",
        "tagline": "Charcoal, black and gold",
        "light": palette("#efeeec", "#ffffff", "#e3e1dc", "#c9c6bd", "#1c1c1c", "#55524a", "#2b2b2b", "#c9a227"),
        "dark": palette("#0d0d0f", "#17171a", "#222226", "#3a3a40", "#f2efe6", "#b9b4a6", "#e0b93a", "#f2d27a"),
    },
    "leafeon": {
        "name": "Leafeon",
        "tagline": "Natural greens and earthy tones",
        "light": palette("#eef4e4", "#fbfdf6", "#dfe9cf", "#c3d3a8", "#1f2d14", "#4d6137", "#3f7d2b", "#a67c3d"),
        "dark": palette("#0f1a0c", "#182614", "#213420", "#365130", "#eaf5df", "#b3cfa0", "#86c96a", "#d2a85f"),
    },
    "glaceon": {
        "name": "Glaceon",
        "tagline": "Icy blue and cool silver",
        "light": palette("#eef5fb", "#ffffff", "#dde9f5", "#c1d2e4", "#17283a", "#4f6479", "#2f6fa8", "#8fb8dc"),
        "dark": palette("#0c1620", "#142231", "#1c2f43", "#2f4a66", "#e9f2fb", "#aec2d6", "#8cc4f0", "#c3d3e3"),
    },
    "sylveon": {
        "name": "Sylveon",
        "tagline": "Soft pink and aqua",
        "light": palette("#fdf0f5", "#fffafc", "#fadde8", "#f0c1d4", "#3a1627", "#7a4660", "#b83a73", "#3bb8c4"),
        "dark": palette("#1f0d17", "#2d1522", "#3b1c2d", "#5a2d45", "#fde9f2", "#e2b5cb", "#f08cb8", "#6fd8e0"),
    },
}

# Medical status colours. They are the same in every theme so that green, amber and
# red always mean the same thing; only light and dark mode change them.
STATUS = {
    "light": {
        "ok-bg": "#e6f4ea",
        "ok-text": "#1e6b34",
        "warn-bg": "#fff4d6",
        "warn-text": "#7a5200",
        "danger-bg": "#fdecea",
        "danger-text": "#a8211a",
        "danger": "#b3261e",
        "danger-on": "#ffffff",
    },
    "dark": {
        "ok-bg": "#143824",
        "ok-text": "#9be3b2",
        "warn-bg": "#3b2c08",
        "warn-text": "#f5cf75",
        "danger-bg": "#4a1a16",
        "danger-text": "#ffaaa0",
        "danger": "#c93a2f",
        "danger-on": "#ffffff",
    },
}

# When printing, always use plain black on white, whatever the theme.
PRINT_CSS = (
    "@media print{:root{--bg:#ffffff;--surface:#ffffff;--surface-alt:#ffffff;"
    "--text:#000000;--muted:#333333;--border:#bbbbbb;--link:#000000;}}"
)


# ---------- Colour helpers ----------


def valid_hex(value: Optional[str]) -> Optional[str]:
    """Return a clean #rrggbb colour, or None. Only valid colours can reach the CSS."""
    text = (value or "").strip()
    return text.lower() if HEX_PATTERN.match(text) else None


def hex_to_rgb(value: str):
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*[max(0, min(255, round(c))) for c in rgb])


def luminance(value: str) -> float:
    channels = []
    for c in hex_to_rgb(value):
        c = c / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours (1 to 21)."""
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix(a: str, b: str, weight: float) -> str:
    """Blend a and b. weight is how much of b to use (0 to 1)."""
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex(tuple(x * (1 - weight) + y * weight for x, y in zip(ra, rb)))


def readable_on(background: str) -> str:
    """Near-black or white, whichever is easier to read on this background."""
    dark, light = "#111111", "#ffffff"
    return light if contrast(light, background) >= contrast(dark, background) else dark


def ensure_contrast(color: str, backgrounds: List[str], ratio: float, lighten: bool) -> str:
    """Nudge a colour lighter or darker until it is readable on every background."""
    target = "#ffffff" if lighten else "#000000"
    for step in range(21):
        candidate = mix(color, target, step * 0.05)
        if all(contrast(candidate, bg) >= ratio for bg in backgrounds):
            return candidate
    return target


# ---------- Building the CSS variables ----------


def build_tokens(
    theme_key: str,
    variant: str,
    primary: Optional[str] = None,
    accent: Optional[str] = None,
) -> Dict[str, str]:
    colors = dict(THEMES[theme_key][variant])
    if primary:
        colors["primary"] = primary
    if accent:
        colors["accent"] = accent
    dark = variant == "dark"

    bg, surface, alt = colors["bg"], colors["surface"], colors["surface_alt"]
    p = colors["primary"]
    soft = mix(surface, p, 0.24 if dark else 0.14)
    link = ensure_contrast(p, [bg, surface, alt, soft], 4.5, dark)
    focus = ensure_contrast(colors["accent"], [bg, surface, alt], 3.0, dark)

    if dark:
        header_bg, header_text, header_link = alt, colors["text"], link
    else:
        header_bg, header_text = p, readable_on(p)
        header_link = header_text

    tokens = {
        "bg": bg,
        "surface": surface,
        "surface-alt": alt,
        "border": colors["border"],
        "input-border": ensure_contrast(colors["border"], [bg, surface], 3.0, dark),
        "text": colors["text"],
        "muted": colors["muted"],
        "primary": p,
        "primary-text": readable_on(p),
        "primary-soft": soft,
        "accent": colors["accent"],
        "link": link,
        "focus": focus,
        "header-bg": header_bg,
        "header-text": header_text,
        "header-link": header_link,
        "header-line": p,
        "chart-bg": surface,
        "chart-grid": colors["border"],
        "chart-axis": colors["muted"],
        "chart-1": ensure_contrast(p, [surface], 3.0, dark),
        "chart-2": ensure_contrast(colors["accent"], [surface], 3.0, dark),
        "shadow": "0 1px 4px rgba(0,0,0,0.5)" if dark else "0 1px 4px rgba(0,0,0,0.12)",
    }
    tokens.update(STATUS[variant])
    return tokens


def css_block(tokens: Dict[str, str], scheme: str) -> str:
    body = "".join(f"--{name}:{value};" for name, value in tokens.items())
    return ":root{color-scheme:" + scheme + ";" + body + "}"


def resolve_appearance(
    theme: Optional[str] = None,
    mode: Optional[str] = None,
    primary: Optional[str] = None,
    accent: Optional[str] = None,
) -> dict:
    """Everything a page needs: the CSS text and a few values for the header."""
    theme = theme if theme in THEMES else DEFAULT_THEME
    mode = mode if mode in MODES else DEFAULT_MODE
    primary = valid_hex(primary)
    accent = valid_hex(accent)

    light = build_tokens(theme, "light", primary, accent)
    dark = build_tokens(theme, "dark", primary, accent)
    light_css = css_block(light, "light")
    dark_css = css_block(dark, "dark")
    if mode == "dark":
        css = dark_css
    elif mode == "system":
        css = light_css + "@media (prefers-color-scheme: dark){" + dark_css + "}"
    else:
        css = light_css

    return {
        "theme": theme,
        "mode": mode,
        "primary": primary,
        "accent": accent,
        "css": css + PRINT_CSS,
        "header_bg": (dark if mode == "dark" else light)["header-bg"],
        "app_name": APP_NAME,
    }


def theme_previews() -> List[dict]:
    """The raw colours of every theme, for the swatches on the Settings page."""
    return [
        {
            "key": key,
            "name": theme["name"],
            "tagline": theme["tagline"],
            "light": theme["light"],
            "dark": theme["dark"],
        }
        for key, theme in THEMES.items()
    ]


# ---------- Contrast check:  python -m app.themes ----------


def check_contrast() -> int:
    rules = [
        ("text on page", "text", "bg", 4.5),
        ("text on card", "text", "surface", 4.5),
        ("muted text on page", "muted", "bg", 4.5),
        ("muted text on card", "muted", "surface", 4.5),
        ("muted text on tinted area", "muted", "surface-alt", 4.5),
        ("button text", "primary-text", "primary", 4.5),
        ("links on page", "link", "bg", 4.5),
        ("links on card", "link", "surface", 4.5),
        ("header text", "header-text", "header-bg", 4.5),
        ("header links", "header-link", "header-bg", 4.5),
        ("focus ring on card", "focus", "surface", 3.0),
        ("chart line 1", "chart-1", "surface", 3.0),
        ("chart line 2", "chart-2", "surface", 3.0),
        ("input border", "input-border", "surface", 3.0),
        ("status: ok", "ok-text", "ok-bg", 4.5),
        ("status: warning", "warn-text", "warn-bg", 4.5),
        ("status: danger", "danger-text", "danger-bg", 4.5),
        ("danger button", "danger-on", "danger", 4.5),
    ]
    checked = 0
    failures = []
    for key in THEMES:
        for variant in ("light", "dark"):
            tokens = build_tokens(key, variant)
            for label, fore, back, needed in rules:
                ratio = contrast(tokens[fore], tokens[back])
                checked += 1
                if ratio < needed:
                    failures.append(
                        f"{key} ({variant}): {label} is {ratio:.2f}, needs {needed}"
                    )
    print(f"Checked {checked} colour pairs across {len(THEMES)} themes.")
    if failures:
        print("These pairs are too faint to read comfortably:")
        for line in failures:
            print("  -", line)
    else:
        print("All pairs passed.")
    return len(failures)


if __name__ == "__main__":
    check_contrast()