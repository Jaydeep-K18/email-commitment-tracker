"""The dashboard's visual layer: palettes, stylesheet, and the cursor spotlight.

Streamlit has no runtime theming API and no way to reach the page's ``<head>``,
so every colour the dashboard uses is declared once here as a CSS custom
property and injected per rerun. Two palettes are defined — light and dark — and
:func:`inject` emits whichever one the session has chosen. Anything that renders
a colour must read it from a variable, otherwise one of the two modes silently
rots.

Three Streamlit-specific facts shape this module, all verified against the
installed 1.60 frontend bundle rather than assumed:

1. ``st.markdown(..., unsafe_allow_html=True)`` renders through react-markdown,
   which never executes ``<script>``. ``st.html(..., unsafe_allow_javascript=True)``
   does: it re-creates each script element with ``document.createElement`` so the
   browser runs it, *in the main document* rather than in a component iframe.
   That is the mechanism the spotlight uses. :func:`_inject_spotlight` keeps the
   older ``components.html`` + ``window.parent`` trick as a fallback for
   Streamlit versions without the flag.
2. ``st.html`` whose body is only ``<style>`` is routed to Streamlit's event
   container, so the stylesheet costs no vertical space in the layout.
3. Streamlit sanitises that HTML with DOMPurify, whose ``SAFE_FOR_XML`` guard
   deletes any element whose text matches ``/<[/\\w!]/``. Injected CSS and JS
   therefore must never contain ``<`` followed by a letter, digit, ``/`` or
   ``!`` — hence the JS below builds nodes with ``createElement`` and compares
   with ``>`` only. Breaking that rule makes the whole block vanish silently.

Streamlit's internal class names are generated and unstable, so every selector
here is a ``data-*``, ARIA, or ``st-key-`` attribute selector. All of the rules
are additive (colour, radius, shadow); if a selector stops matching after an
upgrade the widget falls back to Streamlit's own styling rather than breaking.
"""
from __future__ import annotations

import html as html_escape
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import streamlit as st

from dashboard import data

# --- Themes ---------------------------------------------------------------

LIGHT = "light"
DARK = "dark"
THEMES: tuple[str, str] = (LIGHT, DARK)
DEFAULT_THEME = LIGHT

#: Session-state key holding the chosen theme. Also the toggle widget's key, so
#: the value is already in state when :func:`inject` reads it on the next rerun.
THEME_STATE_KEY = "ect_theme"

THEME_LABELS: dict[str, str] = {LIGHT: "☀︎ Light", DARK: "☾ Dark"}

#: Node colour per VIP tier, mirroring ``dashboard.graph.TIER_COLOUR`` for the
#: light palette. Duplicated rather than imported so this module does not drag
#: networkx into every page load; ``tests/test_styles.py`` asserts they agree.
_LIGHT_TIER = {
    "CRITICAL": "#d92b2b",
    "IMPORTANT": "#e8710a",
    "MONITOR": "#2e6fdb",
    "SKIP": "#9ca3af",
    "untiered": "#6b7280",
}

#: The same tiers lifted for a dark background — the light reds and oranges are
#: too dense to read against #0b1120.
_DARK_TIER = {
    "CRITICAL": "#ff6b6b",
    "IMPORTANT": "#ffa24d",
    "MONITOR": "#6ea8fe",
    "SKIP": "#7f8ba0",
    "untiered": "#9aa8c2",
}

#: Urgency bands lifted for a dark background. The light values come straight
#: from ``dashboard.data.URGENCY_STYLE`` so the feed, the graph and the CSS all
#: speak one colour language.
_DARK_URGENCY = {
    data.OVERDUE: "#ff6b6b",
    data.TODAY: "#ffa24d",
    data.URGENT: "#ffd166",
    data.UPCOMING: "#62d08a",
    data.UNDATED: "#9aa8c2",
}

#: System stacks only — the project's premise is that nothing is fetched from a
#: third party, and a webfont would be exactly that (PROJECT_PLAN.md §16).
_FONT = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", '
    'Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif'
)
_FONT_MONO = (
    'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, '
    '"Liberation Mono", monospace'
)

_SHARED: dict[str, str] = {
    "--ect-font": _FONT,
    "--ect-font-mono": _FONT_MONO,
    "--ect-radius": "14px",
    "--ect-radius-sm": "10px",
    "--ect-radius-pill": "999px",
    # Spotlight geometry. The radius is deliberately larger than it looks: the
    # mask fades to nothing well before its edge, so the lit area reads ~180px.
    "--ect-dot-gap": "26px",
    "--ect-spot-radius": "190px",
    "--ect-dot-rest": "0.04",
    "--ect-dot-peak": "0.33",
}

_LIGHT_PALETTE: dict[str, str] = {
    "--ect-bg": "#f4f6fb",
    "--ect-bg-accent": "rgba(46, 111, 219, 0.05)",
    "--ect-surface": "rgba(255, 255, 255, 0.88)",
    "--ect-surface-solid": "#ffffff",
    "--ect-surface-raised": "#ffffff",
    "--ect-surface-muted": "#eef2f9",
    "--ect-field": "#ffffff",
    "--ect-field-hover": "#f7f9fd",
    "--ect-border": "#dde4ef",
    "--ect-border-strong": "#c2cddf",
    "--ect-text": "#101828",
    "--ect-text-muted": "#546179",
    "--ect-text-faint": "#8593a9",
    "--ect-primary": "#2e6fdb",
    "--ect-primary-strong": "#1f56b3",
    "--ect-primary-soft": "rgba(46, 111, 219, 0.11)",
    "--ect-primary-contrast": "#ffffff",
    "--ect-dot": "#2e6fdb",
    "--ect-shadow": "0 1px 2px rgba(16, 24, 40, 0.04), 0 10px 26px -20px rgba(16, 24, 40, 0.45)",
    "--ect-shadow-hover": (
        "0 2px 4px rgba(16, 24, 40, 0.06), 0 20px 44px -26px rgba(16, 24, 40, 0.55)"
    ),
    "--ect-shadow-menu": "0 18px 48px -20px rgba(16, 24, 40, 0.38)",
}

_DARK_PALETTE: dict[str, str] = {
    "--ect-bg": "#0b1120",
    "--ect-bg-accent": "rgba(110, 168, 254, 0.06)",
    "--ect-surface": "rgba(20, 29, 48, 0.82)",
    "--ect-surface-solid": "#141d30",
    "--ect-surface-raised": "#0f1728",
    "--ect-surface-muted": "#1a2438",
    "--ect-field": "#121b2c",
    "--ect-field-hover": "#182339",
    "--ect-border": "#25314b",
    "--ect-border-strong": "#3a4a6f",
    "--ect-text": "#e7edf9",
    "--ect-text-muted": "#9dabc4",
    "--ect-text-faint": "#6f7f9c",
    "--ect-primary": "#6ea8fe",
    "--ect-primary-strong": "#93c1ff",
    "--ect-primary-soft": "rgba(110, 168, 254, 0.16)",
    "--ect-primary-contrast": "#08121f",
    "--ect-dot": "#7fb0ff",
    "--ect-shadow": "0 1px 2px rgba(0, 0, 0, 0.5), 0 12px 30px -22px rgba(0, 0, 0, 0.9)",
    "--ect-shadow-hover": (
        "0 2px 6px rgba(0, 0, 0, 0.55), 0 22px 50px -26px rgba(0, 0, 0, 0.95)"
    ),
    "--ect-shadow-menu": "0 20px 52px -18px rgba(0, 0, 0, 0.75)",
}


def _with_semantic_colours(
    base: dict[str, str], urgency: dict[str, str], tiers: dict[str, str]
) -> dict[str, str]:
    """Fold the urgency and tier colour language into a palette."""
    palette_values = dict(_SHARED)
    palette_values.update(base)
    for band, colour in urgency.items():
        palette_values[f"--ect-urgency-{band}"] = colour
    for tier, colour in tiers.items():
        palette_values[f"--ect-tier-{tier.lower()}"] = colour
    return palette_values


PALETTES: dict[str, dict[str, str]] = {
    LIGHT: _with_semantic_colours(
        _LIGHT_PALETTE,
        {band: colour for band, (colour, _) in data.URGENCY_STYLE.items()},
        _LIGHT_TIER,
    ),
    DARK: _with_semantic_colours(_DARK_PALETTE, _DARK_URGENCY, _DARK_TIER),
}


def resolve_theme(value: object) -> str:
    """Coerce whatever is in session state into a theme name.

    The toggle can hand back ``None`` (nothing selected) and older sessions can
    hold a name that no longer exists, so an unusable value must degrade to the
    default rather than produce a stylesheet with no variables in it.
    """
    if isinstance(value, str) and value.lower() in THEMES:
        return value.lower()
    return DEFAULT_THEME


def palette(theme: str) -> dict[str, str]:
    """The CSS custom properties for one theme."""
    return dict(PALETTES[resolve_theme(theme)])


#: Query-string parameter mirroring the chosen theme. Session state is wiped by
#: a browser reload, so without this the dashboard would snap back to light
#: every refresh; the URL is the only per-session store that survives one.
THEME_QUERY_KEY = "theme"


def _url_theme() -> str | None:
    """The theme named in the query string, if it names a real one."""
    try:
        value = st.query_params.get(THEME_QUERY_KEY)
    except Exception:  # noqa: BLE001 - no script run context (tests, imports)
        return None
    if isinstance(value, str) and value.lower() in THEMES:
        return value.lower()
    return None


def seed_theme_from_url(*, key: str = THEME_STATE_KEY) -> None:
    """Adopt the URL's theme before the toggle widget is built.

    Must run before :func:`theme_toggle`, because a widget with an explicit
    ``default`` only honours it when its key is absent from session state —
    seeding first is what stops a reload of ``?theme=dark`` flashing light.
    """
    try:
        if key in st.session_state:
            return
    except Exception:  # noqa: BLE001 - no script run context
        return
    from_url = _url_theme()
    if from_url:
        st.session_state[key] = from_url


def current_theme(*, key: str = THEME_STATE_KEY) -> str:
    """The theme this session has chosen, defaulting on the very first run."""
    try:
        if key in st.session_state:
            return resolve_theme(st.session_state[key])
    except Exception:  # noqa: BLE001 - no script run context
        return DEFAULT_THEME
    return resolve_theme(_url_theme())


# --- CSS ------------------------------------------------------------------

def css_variables(theme: str) -> str:
    """The ``:root`` block for one theme.

    ``color-scheme`` is set alongside the variables so native chrome — scroll
    bars, focus rings, form controls — flips with the palette. Without it a
    dark page keeps bright white scroll bars.
    """
    name = resolve_theme(theme)
    declarations = "".join(f"{key}:{value};" for key, value in palette(name).items())
    scheme = "dark" if name == DARK else "light"
    return f':root{{color-scheme:{scheme};{declarations}}}'


#: Everything below reads from the variables above, so it is theme-independent.
#: Grouped by what it touches; see the module docstring on selector stability.
_BASE_CSS = """
/* --- page canvas ------------------------------------------------------- */
/* The dot layer sits at z-index -1, which paints above the canvas background
   but below every content box. Streamlit paints its own background on both
   `body` (which it also makes position:relative, so it paints *after* negative
   z-index children) and on the app containers; every one of them has to be
   cleared or the dots are covered up. */
html { background-color: var(--ect-bg) !important; }
body { background: transparent !important; }
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
[data-testid="stBottom"] { background: transparent !important; }
[data-testid="stHeader"] {
  background: transparent !important;
  -webkit-backdrop-filter: blur(6px);
  backdrop-filter: blur(6px);
}
/* Embedded components (the network graph) draw their own canvas; give them the
   same corner treatment as everything else rather than a raw rectangle. */
[data-testid="stApp"] iframe { border-radius: var(--ect-radius-sm); }

#ect-spotlight {
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  overflow: hidden;
  --ect-mouse-x: -1000px;
  --ect-mouse-y: -1000px;
}
#ect-spotlight .ect-dots {
  position: absolute;
  inset: 0;
  background-image: radial-gradient(var(--ect-dot) 1px, transparent 1.5px);
  background-size: var(--ect-dot-gap) var(--ect-dot-gap);
}
#ect-spotlight .ect-dots-rest { opacity: var(--ect-dot-rest); }
#ect-spotlight .ect-dots-spot {
  opacity: 0;
  transition: opacity 420ms ease;
  -webkit-mask-image: radial-gradient(
    circle var(--ect-spot-radius) at var(--ect-mouse-x) var(--ect-mouse-y),
    rgba(0, 0, 0, 1) 0%,
    rgba(0, 0, 0, 0.72) 40%,
    rgba(0, 0, 0, 0.24) 68%,
    rgba(0, 0, 0, 0) 100%);
  mask-image: radial-gradient(
    circle var(--ect-spot-radius) at var(--ect-mouse-x) var(--ect-mouse-y),
    rgba(0, 0, 0, 1) 0%,
    rgba(0, 0, 0, 0.72) 40%,
    rgba(0, 0, 0, 0.24) 68%,
    rgba(0, 0, 0, 0) 100%);
}
#ect-spotlight.ect-live .ect-dots-spot { opacity: var(--ect-dot-peak); }
@media (prefers-reduced-motion: reduce) {
  #ect-spotlight .ect-dots-spot { transition: none; }
}

/* The script that installs the layer arrives as an st.html element; it hides
   its own container, and this rule is the belt to that braces. */
[data-testid="stElementContainer"]:has(> [data-testid="stHtml"] > script) {
  display: none !important;
}

/* --- typography -------------------------------------------------------- */
/* Form controls do not inherit a font, and Streamlit sets one on each of them,
   so the stack has to be restated rather than left to cascade from the app
   root. Icon spans carry their own font-family and are deliberately untouched.
   The dropdown menus are portalled outside the app root, hence the last two. */
body,
[data-testid="stApp"] button,
[data-testid="stApp"] input,
[data-testid="stApp"] textarea,
[data-testid="stSelectboxVirtualDropdown"],
div[data-baseweb="popover"] [data-baseweb="menu"] {
  font-family: var(--ect-font);
}
[data-testid="stApp"] { color: var(--ect-text); }
[data-testid="stApp"] h1,
[data-testid="stApp"] h2,
[data-testid="stApp"] h3,
[data-testid="stApp"] h4,
[data-testid="stApp"] h5,
[data-testid="stApp"] h6,
[data-testid="stHeading"] {
  font-family: var(--ect-font);
  color: var(--ect-text) !important;
  letter-spacing: -0.015em;
}
[data-testid="stApp"] h1 { font-weight: 650; }
[data-testid="stMarkdownContainer"] { color: var(--ect-text); }
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {
  color: var(--ect-text-muted) !important;
}
[data-testid="stApp"] a { color: var(--ect-primary); }
[data-testid="stApp"] hr,
[data-testid="stHeadingDivider"] {
  border-color: var(--ect-border) !important;
  background: var(--ect-border) !important;
}
[data-testid="stApp"] code,
[data-testid="stCode"],
[data-testid="stCode"] pre,
[data-testid="stCode"] code {
  font-family: var(--ect-font-mono);
}
[data-testid="stMarkdownContainer"] code {
  background: var(--ect-primary-soft) !important;
  color: var(--ect-primary-strong) !important;
  border-radius: 6px;
  padding: 0.08em 0.36em;
}
[data-testid="stCode"] pre,
[data-testid="stCode"] > div {
  background: var(--ect-surface-muted) !important;
  border: 1px solid var(--ect-border) !important;
  border-radius: var(--ect-radius-sm) !important;
}
[data-testid="stCode"] code,
[data-testid="stCode"] code span { color: var(--ect-text) !important; }

/* --- sidebar ----------------------------------------------------------- */
[data-testid="stSidebar"] {
  background: var(--ect-surface-raised) !important;
  border-right: 1px solid var(--ect-border) !important;
}
[data-testid="stSidebarContent"],
[data-testid="stSidebarUserContent"],
[data-testid="stSidebarHeader"] { background: transparent !important; }
[data-testid="stSidebarNav"] a,
[data-testid="stSidebarNavItems"] a {
  border-radius: var(--ect-radius-sm);
  color: var(--ect-text-muted) !important;
  transition: background-color 130ms ease, color 130ms ease;
}
[data-testid="stSidebarNav"] a:hover,
[data-testid="stSidebarNavItems"] a:hover {
  background: var(--ect-primary-soft) !important;
  color: var(--ect-primary-strong) !important;
}
[data-testid="stSidebarNav"] a[aria-current="page"],
[data-testid="stSidebarNavItems"] a[aria-current="page"] {
  background: var(--ect-primary-soft) !important;
  color: var(--ect-primary-strong) !important;
  font-weight: 600;
}

/* --- containers -------------------------------------------------------- */
/* Only bordered blocks show a border colour, so recolouring every vertical
   block is inert for the rest — there is no attribute marking "border=True". */
[data-testid="stVerticalBlock"],
[data-testid="stColumn"] {
  border-color: var(--ect-border) !important;
  border-radius: var(--ect-radius) !important;
}
[data-testid="stForm"] {
  background: var(--ect-surface) !important;
  border: 1px solid var(--ect-border) !important;
  border-radius: var(--ect-radius) !important;
}

/* Panels and cards opt in by key, which Streamlit turns into an st-key- class.
   The substring match keeps one rule for every panel we create. */
[data-testid="stVerticalBlock"][class*="st-key-ect-panel"] {
  background: var(--ect-surface) !important;
  /* Longhands, not the `border` shorthand. Streamlit's emotion styles set the
     individual border-* longhands, and a shorthand — even an !important one —
     loses to an !important longhand from another rule. Declaring the shorthand
     here left panels on Streamlit's own 0.8px grey, and inconsistently so. */
  border-width: 1px !important;
  border-style: solid !important;
  border-color: var(--ect-border) !important;
  border-radius: var(--ect-radius) !important;
  padding: 1.15rem 1.25rem 1.25rem !important;
  box-shadow: var(--ect-shadow);
  transition: box-shadow 180ms ease, border-color 180ms ease;
}
[data-testid="stVerticalBlock"][class*="st-key-ect-panel"]:hover {
  border-color: var(--ect-border-strong) !important;
  box-shadow: var(--ect-shadow-hover);
}
[data-testid="stVerticalBlock"][class*="st-key-ect-card"] {
  background: var(--ect-surface-solid) !important;
  border-width: 1px !important;
  border-style: solid !important;
  border-color: var(--ect-border) !important;
  border-radius: var(--ect-radius-sm) !important;
  padding: 0.85rem 0.95rem !important;
  box-shadow: none;
  transition: box-shadow 180ms ease, border-color 180ms ease, transform 180ms ease;
}
[data-testid="stVerticalBlock"][class*="st-key-ect-card"]:hover {
  border-color: var(--ect-border-strong) !important;
  box-shadow: var(--ect-shadow);
}

/* --- panel header ------------------------------------------------------ */
.ect-panel-head {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  margin: 0 0 0.15rem;
}
.ect-panel-head .ect-panel-icon { font-size: 1.05rem; line-height: 1; }
/* Scoped through the app root purely to out-specify the blanket h1-h6 rule
   above, which is `!important` so the two would otherwise fight. */
[data-testid="stApp"] .ect-panel-head .ect-panel-title {
  font-size: 0.95rem;
  font-weight: 650;
  letter-spacing: 0.005em;
  color: var(--ect-text);
  margin: 0;
  padding: 0;
}
.ect-panel-head .ect-panel-count {
  margin-left: auto;
  font-size: 0.72rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  padding: 0.1rem 0.5rem;
  border-radius: var(--ect-radius-pill);
  background: var(--ect-primary-soft);
  color: var(--ect-primary-strong);
}
.ect-panel-caption {
  margin: 0.25rem 0 0;
  font-size: 0.8rem;
  line-height: 1.45;
  color: var(--ect-text-muted);
}

/* --- stat row ---------------------------------------------------------- */
.ect-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
  gap: 0.75rem;
  margin: 0.35rem 0 0.25rem;
}
.ect-stat {
  position: relative;
  overflow: hidden;
  padding: 0.85rem 0.95rem 0.9rem;
  border: 1px solid var(--ect-border);
  border-radius: var(--ect-radius);
  background: var(--ect-surface);
  box-shadow: var(--ect-shadow);
  transition: box-shadow 180ms ease, border-color 180ms ease, transform 180ms ease;
}
.ect-stat::before {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--ect-stat-accent, var(--ect-primary));
  opacity: 0.85;
}
.ect-stat:hover {
  border-color: var(--ect-border-strong);
  box-shadow: var(--ect-shadow-hover);
  transform: translateY(-1px);
}
.ect-stat-label {
  display: block;
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--ect-text-faint);
}
.ect-stat-value {
  display: block;
  margin-top: 0.3rem;
  font-size: 1.85rem;
  font-weight: 640;
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.03em;
  color: var(--ect-text);
}
.ect-stat-hint {
  display: block;
  margin-top: 0.3rem;
  font-size: 0.74rem;
  font-weight: 600;
  color: var(--ect-stat-accent, var(--ect-text-muted));
}
.ect-stat-hint-quiet { color: var(--ect-text-faint); font-weight: 500; }

/* --- urgency band headings (feed) -------------------------------------- */
[data-testid="stApp"] .ect-band {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 1.1rem 0 0.45rem;
  padding: 0;
  font-size: 0.95rem;
  font-weight: 650;
  /* Out-specifies the blanket heading colour above, which is !important. */
  color: var(--ect-band-colour, var(--ect-text)) !important;
}
.ect-band .ect-band-rule {
  flex: 1 1 auto;
  height: 1px;
  background: var(--ect-border);
}
.ect-band .ect-band-count {
  font-size: 0.74rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--ect-text-faint);
}

/* --- buttons ----------------------------------------------------------- */
[data-testid="stApp"] button[data-testid^="stBaseButton"] {
  border-radius: var(--ect-radius-sm) !important;
  border-color: var(--ect-border) !important;
  transition: background-color 140ms ease, border-color 140ms ease,
    box-shadow 140ms ease, color 140ms ease;
}
[data-testid="stApp"] button[data-testid="stBaseButton-secondary"],
[data-testid="stApp"] button[data-testid="stBaseButton-secondaryFormSubmit"] {
  background: var(--ect-field) !important;
  color: var(--ect-text) !important;
}
[data-testid="stApp"] button[data-testid="stBaseButton-secondary"]:hover,
[data-testid="stApp"] button[data-testid="stBaseButton-secondaryFormSubmit"]:hover {
  background: var(--ect-primary-soft) !important;
  border-color: var(--ect-primary) !important;
  color: var(--ect-primary-strong) !important;
}
[data-testid="stApp"] button[data-testid="stBaseButton-primary"],
[data-testid="stApp"] button[data-testid="stBaseButton-primaryFormSubmit"] {
  background: var(--ect-primary) !important;
  border-color: var(--ect-primary) !important;
  color: var(--ect-primary-contrast) !important;
}
[data-testid="stApp"] button[data-testid="stBaseButton-primary"]:hover,
[data-testid="stApp"] button[data-testid="stBaseButton-primaryFormSubmit"]:hover {
  background: var(--ect-primary-strong) !important;
  border-color: var(--ect-primary-strong) !important;
}

/* --- text inputs, checkboxes, expanders, alerts ------------------------ */
[data-testid="stTextInputRootElement"],
[data-testid="stNumberInputContainer"],
[data-baseweb="input"] {
  background: var(--ect-field) !important;
  border-color: var(--ect-border) !important;
  border-radius: var(--ect-radius-sm) !important;
  transition: border-color 140ms ease, box-shadow 140ms ease;
}
[data-testid="stTextInputRootElement"]:hover,
[data-baseweb="input"]:hover { border-color: var(--ect-border-strong) !important; }
[data-testid="stTextInputRootElement"]:focus-within,
[data-baseweb="input"]:focus-within {
  border-color: var(--ect-primary) !important;
  box-shadow: 0 0 0 3px var(--ect-primary-soft) !important;
}
[data-testid="stApp"] input,
[data-testid="stApp"] textarea {
  color: var(--ect-text) !important;
  caret-color: var(--ect-primary);
}
[data-testid="stApp"] input::placeholder,
[data-testid="stApp"] textarea::placeholder { color: var(--ect-text-faint) !important; }
[data-testid="stWidgetLabel"] p {
  color: var(--ect-text-muted) !important;
  font-weight: 600;
  font-size: 0.78rem;
  letter-spacing: 0.02em;
}

[data-testid="stExpander"] details,
[data-testid="stExpander"] {
  background: transparent !important;
  border-color: var(--ect-border) !important;
  border-radius: var(--ect-radius-sm) !important;
}
[data-testid="stExpander"] summary { color: var(--ect-text-muted) !important; }
[data-testid="stExpander"] summary:hover { color: var(--ect-primary-strong) !important; }
[data-testid="stExpanderDetails"] { background: transparent !important; }

[data-testid="stAlertContainer"],
[data-testid="stAlert"] {
  border-radius: var(--ect-radius-sm) !important;
  border: 1px solid var(--ect-border) !important;
}

[data-testid="stMetricValue"] { color: var(--ect-text) !important; }
[data-testid="stMetricLabel"] p { color: var(--ect-text-muted) !important; }

/* --- the theme toggle -------------------------------------------------- */
[class*="st-key-ect-theme-toggle"] [data-testid="stButtonGroup"] { gap: 0.25rem; }
[class*="st-key-ect-theme-toggle"] button {
  border-radius: var(--ect-radius-pill) !important;
  border-color: var(--ect-border) !important;
  background: var(--ect-field) !important;
  color: var(--ect-text-muted) !important;
}
/* react-aria marks the active item with a valueless `data-selected`, so this
   has to be an attribute-presence test rather than `="true"`. */
[class*="st-key-ect-theme-toggle"] button[aria-pressed="true"],
[class*="st-key-ect-theme-toggle"] button[aria-checked="true"],
[class*="st-key-ect-theme-toggle"] button[data-selected] {
  background: var(--ect-primary-soft) !important;
  border-color: var(--ect-primary) !important;
  color: var(--ect-primary-strong) !important;
}

/* --- dropdowns --------------------------------------------------------- */
/* Two different widget implementations ship in Streamlit 1.60: st.multiselect
   is still BaseWeb (data-baseweb="select"/"tag"), while st.selectbox is
   react-aria (role="combobox" plus data-focused/data-open/data-selected). Both
   are covered; whichever stops matching simply keeps Streamlit's own look. */
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div,
[data-testid="stSelectbox"] [role="combobox"] {
  background: var(--ect-field) !important;
  border-color: var(--ect-border) !important;
  border-radius: var(--ect-radius-sm) !important;
  color: var(--ect-text) !important;
  transition: border-color 140ms ease, box-shadow 140ms ease,
    background-color 140ms ease;
}
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div:hover,
[data-testid="stSelectbox"] [role="combobox"]:hover {
  border-color: var(--ect-border-strong) !important;
  background: var(--ect-field-hover) !important;
}
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div:focus-within,
[data-testid="stSelectbox"] [role="combobox"][data-focused],
[data-testid="stSelectbox"] [role="combobox"][data-open] {
  border-color: var(--ect-primary) !important;
  box-shadow: 0 0 0 3px var(--ect-primary-soft) !important;
  background: var(--ect-field) !important;
}
[data-testid="stMultiSelect"] [data-baseweb="select"] svg,
[data-testid="stSelectbox"] [role="combobox"] svg {
  color: var(--ect-text-faint) !important;
  fill: var(--ect-text-faint) !important;
  transition: color 140ms ease, transform 180ms ease;
}
[data-testid="stSelectbox"] [role="combobox"][data-open] svg { transform: rotate(180deg); }

/* Selected-value chips in a multiselect. */
[data-testid="stMultiSelect"] [data-baseweb="tag"] {
  background: var(--ect-primary-soft) !important;
  color: var(--ect-primary-strong) !important;
  border: 1px solid var(--ect-primary) !important;
  border-radius: var(--ect-radius-pill) !important;
  font-weight: 600 !important;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"] span,
[data-testid="stMultiSelect"] [data-baseweb="tag"] svg {
  color: currentColor !important;
  fill: currentColor !important;
}

/* The open menu. Streamlit portals it out of the widget, so it has to be
   addressed globally rather than as a descendant of the select. */
[data-testid="stSelectboxVirtualDropdown"],
div[data-baseweb="popover"] [data-baseweb="menu"] {
  background: var(--ect-surface-solid) !important;
  border: 1px solid var(--ect-border) !important;
  border-radius: var(--ect-radius) !important;
  box-shadow: var(--ect-shadow-menu) !important;
  padding: 0.3rem !important;
}
[data-testid="stSelectboxVirtualDropdown"] [role="option"],
div[data-baseweb="popover"] li[role="option"] {
  border-radius: var(--ect-radius-sm) !important;
  margin: 1px 0 !important;
  color: var(--ect-text) !important;
  background: transparent !important;
  transition: background-color 120ms ease, color 120ms ease;
}
[data-testid="stSelectboxVirtualDropdown"] [role="option"]:hover,
[data-testid="stSelectboxVirtualDropdown"] [role="option"][data-hovered],
[data-testid="stSelectboxVirtualDropdown"] [role="option"][data-focused],
div[data-baseweb="popover"] li[role="option"]:hover,
div[data-baseweb="popover"] li[role="option"][aria-selected="true"] {
  background: var(--ect-primary-soft) !important;
  color: var(--ect-primary-strong) !important;
}
[data-testid="stSelectboxVirtualDropdown"] [role="option"][data-selected] {
  font-weight: 650 !important;
  color: var(--ect-primary-strong) !important;
}
[data-testid="stSelectboxVirtualDropdownEmpty"] { color: var(--ect-text-faint) !important; }

/* --- scrollbars -------------------------------------------------------- */
[data-testid="stApp"] ::-webkit-scrollbar { width: 10px; height: 10px; }
[data-testid="stApp"] ::-webkit-scrollbar-thumb {
  background: var(--ect-border-strong);
  border-radius: var(--ect-radius-pill);
  border: 3px solid transparent;
  background-clip: content-box;
}
[data-testid="stApp"] ::-webkit-scrollbar-track { background: transparent; }
"""


def stylesheet(theme: str) -> str:
    """The complete stylesheet for one theme."""
    return css_variables(theme) + _BASE_CSS


# --- The cursor spotlight -------------------------------------------------

#: Installed once into the page that owns the visible document. Every ``<``
#: here is followed by a space so DOMPurify's SAFE_FOR_XML guard cannot mistake
#: the script's text for markup and delete the whole element — see the module
#: docstring. For the same reason the layer is built with ``createElement``
#: rather than ``innerHTML``.
_SPOTLIGHT_JS = """
(function (root, own) {
  "use strict";

  /* The bootstrap element is pure plumbing; keep it out of the layout. */
  if (own && own.closest) {
    var host = own.closest('[data-testid="stElementContainer"]');
    if (host && host.style) { host.style.display = "none"; }
  }

  var doc = root.document;
  if (!doc || !doc.body) { return; }
  /* Streamlit reruns re-mount elements, so injection must be idempotent. */
  if (doc.getElementById("ect-spotlight")) { return; }

  var layer = doc.createElement("div");
  layer.id = "ect-spotlight";
  layer.setAttribute("aria-hidden", "true");
  var rest = doc.createElement("div");
  rest.className = "ect-dots ect-dots-rest";
  var spot = doc.createElement("div");
  spot.className = "ect-dots ect-dots-spot";
  layer.appendChild(rest);
  layer.appendChild(spot);
  doc.body.appendChild(layer);

  var reduced = false;
  if (root.matchMedia) {
    reduced = root.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  var ease = reduced ? 1 : 0.22;

  var targetX = -1000, targetY = -1000;
  var atX = -1000, atY = -1000;
  var queued = false, seen = false;

  function schedule() {
    if (queued) { return; }
    queued = true;
    if (root.requestAnimationFrame) { root.requestAnimationFrame(paint); }
    else { root.setTimeout(paint, 16); }
  }

  /* All the work happens on a frame, so a burst of mousemove events collapses
     into one style write per repaint. */
  function paint() {
    queued = false;
    var dx = targetX - atX;
    var dy = targetY - atY;
    if (Math.abs(dx) + Math.abs(dy) > 0.4) {
      atX = atX + dx * ease;
      atY = atY + dy * ease;
      schedule();
    } else {
      atX = targetX;
      atY = targetY;
    }
    layer.style.setProperty("--ect-mouse-x", atX.toFixed(1) + "px");
    layer.style.setProperty("--ect-mouse-y", atY.toFixed(1) + "px");
  }

  function onMove(event) {
    if (!seen) {
      seen = true;
      atX = event.clientX;
      atY = event.clientY;
    }
    targetX = event.clientX;
    targetY = event.clientY;
    layer.classList.add("ect-live");
    schedule();
  }

  function dim() { layer.classList.remove("ect-live"); }

  doc.addEventListener("mousemove", onMove, { passive: true });
  doc.addEventListener("mouseleave", dim);
  /* Moving onto an embedded iframe (the network graph) stops mousemove
     reaching us, so fade out rather than leaving a halo stranded. */
  root.addEventListener("blur", dim);
})(%(root)s, %(own)s);
"""


def spotlight_script(*, root: str = "window", own: str = "document.currentScript") -> str:
    """The spotlight installer, targeted at ``root``'s document.

    ``root`` is ``window`` when the script runs in the app's own document and
    ``window.parent`` when it has to be smuggled in through a component iframe.
    """
    return _SPOTLIGHT_JS % {"root": root, "own": own}


def _inject_spotlight() -> None:
    """Run the spotlight installer, whichever mechanism this Streamlit offers."""
    try:
        st.html(
            f"<script>{spotlight_script()}</script>",
            unsafe_allow_javascript=True,
        )
    except TypeError:
        # Streamlit older than 1.53 has no unsafe_allow_javascript. Fall back to
        # a zero-height component iframe, which is same-origin with the app and
        # can therefore reach the real document through window.parent.
        import streamlit.components.v1 as components

        components.html(
            "<script>"
            + spotlight_script(root="window.parent", own="document.currentScript")
            + "</script>",
            height=0,
        )


# --- Public entry points --------------------------------------------------

def inject(theme: str | None = None) -> str:
    """Emit the stylesheet and the spotlight for this rerun.

    Call once, immediately after ``st.set_page_config``. Returns the theme that
    was applied so callers can branch on it if they need to.
    """
    if theme is None:
        seed_theme_from_url()
    active = resolve_theme(theme) if theme is not None else current_theme()
    # A style-only body goes to Streamlit's event container, which means the
    # stylesheet occupies no vertical space in the page.
    st.html(f"<style>{stylesheet(active)}</style>")
    _inject_spotlight()
    return active


def theme_toggle(*, key: str = THEME_STATE_KEY) -> str:
    """Render the sun/moon switch. Call inside the sidebar.

    Selecting a value reruns the script, and because the widget owns ``key``
    the new theme is already in session state when :func:`inject` runs at the
    top of that rerun — so the page repaints in one pass.
    """
    with st.container(key="ect-theme-toggle"):
        choice = st.segmented_control(
            "Appearance",
            options=list(THEMES),
            format_func=lambda name: THEME_LABELS[name],
            default=DEFAULT_THEME,
            selection_mode="single",
            required=True,
            key=key,
            label_visibility="collapsed",
            width="stretch",
        )
    active = resolve_theme(choice)
    # Mirror into the URL so the choice outlives a refresh. Written only on a
    # real change: assigning every rerun would fight the user's own navigation.
    try:
        if st.query_params.get(THEME_QUERY_KEY) != active:
            st.query_params[THEME_QUERY_KEY] = active
    except Exception:  # noqa: BLE001 - no script run context
        pass
    return active


# --- Presentation helpers -------------------------------------------------

@dataclass(frozen=True)
class Stat:
    """One tile in a stat row.

    ``tone`` names a colour variable suffix (an urgency band, ``primary`` or
    ``neutral``); ``hint`` is the small line under the number.
    """

    label: str
    value: object
    tone: str = "primary"
    hint: str | None = None
    #: Quiet hints are context ("of 12 open"); loud ones are a call to action.
    loud_hint: bool = False


#: Tone name to the CSS variable that paints the tile's accent rule.
_TONE_VARIABLES: dict[str, str] = {
    "primary": "--ect-primary",
    "neutral": "--ect-text-faint",
    data.OVERDUE: f"--ect-urgency-{data.OVERDUE}",
    data.TODAY: f"--ect-urgency-{data.TODAY}",
    data.URGENT: f"--ect-urgency-{data.URGENT}",
    data.UPCOMING: f"--ect-urgency-{data.UPCOMING}",
    data.UNDATED: f"--ect-urgency-{data.UNDATED}",
}


def tone_variable(tone: str) -> str:
    """CSS variable for a tone, falling back to the primary accent."""
    return _TONE_VARIABLES.get(tone, "--ect-primary")


def stat_row_html(stats: Sequence[Stat]) -> str:
    """Markup for a row of stat tiles.

    Kept separate from :func:`stat_row` so the escaping and the "every value
    survives" guarantee can be asserted without a Streamlit runtime.
    """
    tiles = []
    for stat in stats:
        accent = tone_variable(stat.tone)
        parts = [
            f'<span class="ect-stat-label">{html_escape.escape(str(stat.label))}</span>',
            f'<span class="ect-stat-value">{html_escape.escape(str(stat.value))}</span>',
        ]
        if stat.hint:
            quiet = "" if stat.loud_hint else " ect-stat-hint-quiet"
            parts.append(
                f'<span class="ect-stat-hint{quiet}">'
                f"{html_escape.escape(str(stat.hint))}</span>"
            )
        tiles.append(
            f'<div class="ect-stat" style="--ect-stat-accent: var({accent})">'
            + "".join(parts)
            + "</div>"
        )
    return '<div class="ect-stats">' + "".join(tiles) + "</div>"


def stat_row(stats: Sequence[Stat]) -> None:
    """Render a row of stat tiles."""
    st.html(stat_row_html(stats))


def panel_header_html(
    title: str, *, icon: str | None = None, count: int | None = None,
    caption: str | None = None,
) -> str:
    """Markup for a panel's title row."""
    head = ['<div class="ect-panel-head">']
    if icon:
        head.append(f'<span class="ect-panel-icon">{html_escape.escape(icon)}</span>')
    # A real heading, not a styled span: these replace st.subheader, and the
    # page's outline should still make sense to a screen reader.
    head.append(
        f'<h3 class="ect-panel-title">{html_escape.escape(title)}</h3>'
    )
    if count is not None:
        head.append(f'<span class="ect-panel-count">{int(count)}</span>')
    head.append("</div>")
    if caption:
        head.append(
            f'<p class="ect-panel-caption">{html_escape.escape(caption)}</p>'
        )
    return "".join(head)


@contextmanager
def panel(
    title: str,
    *,
    key: str,
    icon: str | None = None,
    count: int | None = None,
    caption: str | None = None,
) -> Iterator[None]:
    """A bordered section with a title row. ``key`` must start with ``ect-panel``.

    The prefix is what the stylesheet matches on, so a key that does not carry
    it would render as a plain Streamlit container.
    """
    with st.container(border=True, key=key):
        st.html(panel_header_html(title, icon=icon, count=count, caption=caption))
        yield


def band_heading_html(band: str, count: int) -> str:
    """A feed section heading coloured by urgency band.

    The colour comes from the palette rather than from ``URGENCY_STYLE``
    directly: the stored hexes are tuned for a white page and are muddy on a
    dark one, and only the CSS variable knows which mode is active.
    """
    _, label = data.URGENCY_STYLE[band]
    icon = data.URGENCY_ICON[band]
    return (
        f'<h4 class="ect-band" style="--ect-band-colour: var(--ect-urgency-{band})">'
        f'<span class="ect-band-icon">{icon}</span>'
        f"<span>{html_escape.escape(label)}</span>"
        f'<span class="ect-band-count">{int(count)}</span>'
        f'<span class="ect-band-rule"></span>'
        f"</h4>"
    )


def band_heading(band: str, count: int) -> None:
    """Render a feed section heading."""
    st.html(band_heading_html(band, count))
