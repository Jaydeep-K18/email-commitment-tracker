"""Tests for the dashboard's visual layer (``dashboard/styles.py``).

Nothing here needs a browser: the palettes, the generated CSS and the generated
markup are all plain strings, so the parts that silently break — a colour that
only exists in one theme, an injected block that Streamlit's sanitiser would
delete, a number that fell out of a stat tile — can be asserted directly.
"""
from __future__ import annotations

import re

import pytest
from streamlit.testing.v1 import AppTest

from dashboard import data, styles

#: Streamlit sanitises injected HTML with DOMPurify, whose SAFE_FOR_XML guard
#: force-removes any element whose text matches this. A ``<`` followed by a
#: letter, digit, ``/`` or ``!`` anywhere in the CSS or the JS would therefore
#: make the entire <style>/<script> block vanish from the page with no error —
#: the dashboard would just render unstyled. See dashboard/styles.py's docstring.
SANITISER_TRIPWIRE = re.compile(r"<[/\w!]")


# --- Theme resolution ------------------------------------------------------

@pytest.mark.parametrize("name", styles.THEMES)
def test_a_known_theme_name_resolves_to_itself(name):
    assert styles.resolve_theme(name) == name


def test_theme_names_are_matched_case_insensitively():
    assert styles.resolve_theme("DARK") == styles.DARK


@pytest.mark.parametrize("value", [None, "", "solarized", 7, ["dark"]])
def test_an_unusable_theme_choice_falls_back_to_the_default(value):
    """The toggle hands back None when nothing is selected, and a stored choice
    can outlive the theme it names. Either way a stylesheet must still be
    produced — an empty variable block would leave the page with no colours."""
    assert styles.resolve_theme(value) == styles.DEFAULT_THEME


# --- Palettes --------------------------------------------------------------

def test_both_themes_define_exactly_the_same_variables():
    """A variable present in one palette only is invisible until someone opens
    the other mode, so the sets are asserted equal rather than merely non-empty."""
    assert set(styles.palette(styles.LIGHT)) == set(styles.palette(styles.DARK))


@pytest.mark.parametrize("name", styles.THEMES)
def test_every_palette_entry_is_a_custom_property(name):
    assert all(key.startswith("--") for key in styles.palette(name))


def test_no_colour_is_shared_between_the_two_modes_by_accident():
    """The shared entries are the non-colour ones (fonts, radii, opacities);
    everything that names a colour must actually differ between the modes."""
    light = styles.palette(styles.LIGHT)
    dark = styles.palette(styles.DARK)
    identical = {key for key in light if light[key] == dark[key]}
    assert not {key for key in identical if "colour" in key or key.endswith("-bg")}


def test_the_light_urgency_colours_are_the_ones_the_rest_of_the_app_uses():
    """URGENCY_STYLE drives the feed and the graph edges; the CSS has to agree
    with it or the same commitment would be two different colours."""
    light = styles.palette(styles.LIGHT)
    for band, (colour, _) in data.URGENCY_STYLE.items():
        assert light[f"--ect-urgency-{band}"] == colour


def test_every_urgency_band_has_a_dark_variant():
    dark = styles.palette(styles.DARK)
    for band in data.URGENCY_STYLE:
        assert dark[f"--ect-urgency-{band}"]


def test_the_light_tier_colours_still_match_the_graphs():
    """dashboard/styles.py copies TIER_COLOUR rather than importing it, to keep
    networkx off the import path of every page. This is the guard on that copy."""
    from dashboard import graph

    light = styles.palette(styles.LIGHT)
    for tier, colour in graph.TIER_COLOUR.items():
        assert light[f"--ect-tier-{tier.lower()}"] == colour
    assert light["--ect-tier-untiered"] == graph.UNTIERED_COLOUR


def test_the_dot_grid_is_faint_at_rest_and_brighter_under_the_cursor():
    for name in styles.THEMES:
        values = styles.palette(name)
        rest = float(values["--ect-dot-rest"])
        peak = float(values["--ect-dot-peak"])
        assert 0 < rest <= 0.06
        assert 0.28 <= peak <= 0.40


# --- Generated CSS ---------------------------------------------------------

@pytest.mark.parametrize("name", styles.THEMES)
def test_the_variable_block_declares_every_palette_entry(name):
    block = styles.css_variables(name)
    for key, value in styles.palette(name).items():
        assert f"{key}:{value};" in block


def test_the_colour_scheme_follows_the_theme():
    """Without this, a dark page keeps bright white native scrollbars."""
    assert "color-scheme:dark" in styles.css_variables(styles.DARK)
    assert "color-scheme:light" in styles.css_variables(styles.LIGHT)


@pytest.mark.parametrize("name", styles.THEMES)
def test_the_stylesheet_survives_streamlits_sanitiser(name):
    assert not SANITISER_TRIPWIRE.search(styles.stylesheet(name))


def test_the_stylesheet_hard_codes_no_colours():
    """Every colour must come from a variable, otherwise one of the two modes
    is wrong. The variable block is the only place a literal may appear — plus
    the spotlight's mask, where rgba(0,0,0,a) is an alpha ramp and the channel
    values are ignored by the compositor."""
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", styles._BASE_CSS)
    literals = set(re.findall(r"rgba?\([^)]*\)", styles._BASE_CSS))
    assert all(literal.startswith("rgba(0, 0, 0,") for literal in literals)


def test_the_stylesheet_fetches_nothing_over_the_network():
    """The project's premise is that nothing phones out (PROJECT_PLAN.md §16)."""
    for name in styles.THEMES:
        sheet = styles.stylesheet(name)
        assert "//" not in sheet.replace("://", "")
        assert "url(" not in sheet
        assert "@import" not in sheet


# --- The spotlight script --------------------------------------------------

def test_the_spotlight_script_survives_streamlits_sanitiser():
    assert not SANITISER_TRIPWIRE.search(styles.spotlight_script())


def test_the_spotlight_can_be_aimed_at_a_parent_document():
    """The component-iframe fallback runs inside an iframe and has to reach the
    real page through window.parent."""
    assert styles.spotlight_script(root="window.parent").rstrip().endswith(
        "(window.parent, document.currentScript);"
    )


def test_the_spotlight_refuses_to_install_itself_twice():
    """Streamlit re-mounts elements across reruns; without the guard every
    rerun would stack another dot layer and another mousemove listener."""
    script = styles.spotlight_script()
    assert 'getElementById("ect-spotlight")' in script
    assert "return" in script


def test_the_spotlight_animates_on_the_target_windows_frames():
    """requestAnimationFrame is throttled to nothing in a hidden document, and
    the bootstrap element hides itself — so the frames must be asked for on the
    window that owns the visible page, not on whatever window ran the script."""
    assert "root.requestAnimationFrame(paint)" in styles.spotlight_script()


# --- Stat tiles ------------------------------------------------------------

def test_a_stat_tile_keeps_its_label_value_and_hint():
    html = styles.stat_row_html([
        styles.Stat("Due this week", 12, tone=data.URGENT, hint="3 are yours")
    ])
    assert "Due this week" in html
    assert ">12<" in html
    assert "3 are yours" in html


def test_a_stat_tile_without_a_hint_renders_no_hint_element():
    html = styles.stat_row_html([styles.Stat("You owe", 0)])
    assert "ect-stat-hint" not in html


def test_a_stat_tile_is_accented_by_its_tone():
    html = styles.stat_row_html([styles.Stat("Overdue", 2, tone=data.OVERDUE)])
    assert f"var(--ect-urgency-{data.OVERDUE})" in html


def test_an_unknown_tone_still_produces_a_usable_accent():
    """A typo in a tone name must not leave the tile with `var()` pointing at
    nothing, which renders as a transparent rule."""
    assert styles.tone_variable("chartreuse") == "--ect-primary"


def test_stat_content_is_escaped():
    html = styles.stat_row_html([styles.Stat("A & B", "<b>7</b>")])
    assert "A &amp; B" in html
    assert "<b>" not in html


def test_a_row_renders_one_tile_per_stat():
    html = styles.stat_row_html([
        styles.Stat("a", 1), styles.Stat("b", 2), styles.Stat("c", 3)
    ])
    assert html.count('class="ect-stat"') == 3


# --- Panels and band headings ----------------------------------------------

def test_a_panel_header_carries_its_title_icon_count_and_caption():
    html = styles.panel_header_html(
        "Next on your calendar", icon="📅", count=5, caption="only here"
    )
    assert "Next on your calendar" in html
    assert "📅" in html
    assert ">5<" in html
    assert "only here" in html


def test_a_panel_header_omits_a_count_of_none_rather_than_showing_zero():
    """None means "do not count", which is not the same as a count of zero."""
    assert "ect-panel-count" not in styles.panel_header_html("Filters")


def test_a_band_heading_uses_the_palette_not_the_stored_hex():
    """URGENCY_STYLE's hexes are tuned for a white page. Referring to the
    variable is what lets the same heading stay legible in dark mode."""
    html = styles.band_heading_html(data.UPCOMING, 4)
    assert f"var(--ect-urgency-{data.UPCOMING})" in html
    assert data.URGENCY_STYLE[data.UPCOMING][0] not in html


def test_a_band_heading_shows_the_bands_label_icon_and_count():
    html = styles.band_heading_html(data.OVERDUE, 3)
    colour, label = data.URGENCY_STYLE[data.OVERDUE]
    assert label in html
    assert data.URGENCY_ICON[data.OVERDUE] in html
    assert ">3<" in html


# --- Theme persistence across a reload -------------------------------------

def _theme_app():
    """A minimal app exercising the seed -> toggle -> URL round trip."""
    def script():
        import streamlit as st

        from dashboard import styles

        styles.seed_theme_from_url()
        st.text(styles.current_theme())
        styles.theme_toggle()

    # Generous timeout for the same reason as tests/test_notifications.py: the
    # default 3 seconds has to cover importing dashboard.styles and everything
    # under it, which is a cold-import cost rather than anything being tested.
    return AppTest.from_function(script, default_timeout=30)


def test_the_theme_defaults_to_light_with_no_url_hint():
    app = _theme_app().run()
    assert app.text[0].value == styles.DEFAULT_THEME


def test_a_theme_in_the_url_survives_a_reload():
    """Session state is wiped by a browser refresh; the URL is not.

    Without the seed, the toggle's own ``default`` would win and the page would
    snap back to light on every refresh.
    """
    app = _theme_app()
    app.query_params[styles.THEME_QUERY_KEY] = "dark"
    app.run()
    assert app.text[0].value == "dark"


def test_choosing_a_theme_writes_it_into_the_url():
    app = _theme_app().run()
    app.segmented_control[0].set_value("dark").run()
    # AppTest hands query params back in their raw list form.
    written = app.query_params[styles.THEME_QUERY_KEY]
    assert (written[0] if isinstance(written, list) else written) == "dark"


def test_a_nonsense_theme_in_the_url_is_ignored():
    """A hand-edited or stale URL must not produce an unstyled page."""
    app = _theme_app()
    app.query_params[styles.THEME_QUERY_KEY] = "solarized"
    app.run()
    assert app.text[0].value == styles.DEFAULT_THEME


def test_seeding_does_not_override_a_choice_already_made_this_session():
    app = _theme_app()
    app.query_params[styles.THEME_QUERY_KEY] = "dark"
    app.run()
    app.segmented_control[0].set_value("light").run()
    assert app.text[0].value == "light"
