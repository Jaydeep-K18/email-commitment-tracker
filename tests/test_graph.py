"""Tests for the commitment graph (Phase 7)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from dashboard import data
from dashboard import graph as g
from src.storage.models import Commitment

NOW = datetime(2026, 7, 30, 12, 0)


def make(**overrides) -> Commitment:
    fields = dict(
        id=1,
        email_id=1,
        type="deadline_on_you",
        subject="Send the report",
        deadline=NOW + timedelta(days=5),
        counterparty_name="Alice Chen",
        counterparty_email="alice@x.com",
        direction="outgoing",
        evidence_quote="please send the report",
        confidence=0.9,
        vip_tier="CRITICAL",
        status="pending",
    )
    fields.update(overrides)
    return Commitment(**fields)


# --- Identity --------------------------------------------------------------

def test_a_person_is_identified_by_address_not_display_name():
    """The same address written with two name spellings is one person."""
    first = make(counterparty_name="Alice Chen", counterparty_email="alice@x.com")
    second = make(counterparty_name="ALICE CHEN", counterparty_email="Alice@X.com")
    assert g.person_key(first) == g.person_key(second)


def test_falls_back_to_the_name_when_there_is_no_address():
    commitment = make(counterparty_email=None, counterparty_name="Alice Chen")
    assert g.person_key(commitment) == "alice chen"
    assert g.person_label(commitment) == "Alice Chen"


def test_an_anonymous_counterparty_still_gets_a_node():
    commitment = make(counterparty_email=None, counterparty_name=None)
    assert g.person_key(commitment) == "unknown"
    graph = g.build_graph([commitment])
    assert graph.number_of_nodes() == 2


def test_the_fuller_name_wins_when_spellings_differ():
    graph = g.build_graph([
        make(id=1, counterparty_name="Alice"),
        make(id=2, counterparty_name="Alice Chen"),
    ])
    assert graph.nodes["alice@x.com"]["label"] == "Alice Chen"


# --- Direction -------------------------------------------------------------

def test_a_deadline_on_you_points_from_you_to_them():
    assert g.edge_direction(make(type="deadline_on_you")) == (g.YOU, "alice@x.com")


def test_a_deadline_from_others_points_at_you():
    assert g.edge_direction(make(type="deadline_from_others")) == (
        "alice@x.com", g.YOU,
    )


def test_a_question_points_at_you_because_you_owe_the_reply():
    assert g.edge_direction(make(type="question_pending")) == ("alice@x.com", g.YOU)


def test_a_meeting_is_drawn_from_you():
    assert g.edge_direction(make(type="meeting")) == (g.YOU, "alice@x.com")


# --- Structure -------------------------------------------------------------

def test_every_commitment_becomes_its_own_edge():
    """Two deadlines with one person must not collapse into a single arrow."""
    graph = g.build_graph([
        make(id=1, subject="Send the report"),
        make(id=2, subject="Send the slides"),
    ])
    assert graph.number_of_nodes() == 2   # You + Alice
    assert graph.number_of_edges() == 2


def test_the_user_node_is_always_present_and_distinct():
    graph = g.build_graph([make()])
    assert g.YOU in graph
    assert graph.nodes[g.YOU]["is_user"] is True
    assert graph.nodes["alice@x.com"]["is_user"] is False


def test_an_empty_input_produces_an_empty_graph():
    graph = g.build_graph([])
    assert graph.number_of_nodes() == 0
    assert g.graph_stats(graph).commitments == 0


def test_node_size_grows_with_the_relationship():
    one = g.build_graph([make(id=1)])
    many = g.build_graph([make(id=i) for i in range(1, 6)])
    assert many.nodes["alice@x.com"]["size"] > one.nodes["alice@x.com"]["size"]


def test_node_size_is_capped_so_one_contact_cannot_dominate():
    huge = g.build_graph([make(id=i) for i in range(1, 60)])
    assert huge.nodes["alice@x.com"]["size"] <= 14 + 12 * 2


def test_a_node_takes_the_colour_of_its_most_urgent_tier():
    graph = g.build_graph([
        make(id=1, vip_tier="MONITOR"),
        make(id=2, vip_tier="CRITICAL"),
    ])
    assert graph.nodes["alice@x.com"]["tier"] == "CRITICAL"
    assert graph.nodes["alice@x.com"]["color"] == g.TIER_COLOUR["CRITICAL"]


def test_an_untiered_person_still_gets_a_colour():
    graph = g.build_graph([make(vip_tier=None)])
    assert graph.nodes["alice@x.com"]["color"] == g.UNTIERED_COLOUR


# --- Edge styling ----------------------------------------------------------

def test_edge_colour_tracks_urgency():
    overdue = g.build_graph([make(deadline=NOW - timedelta(days=3))], now=NOW)
    edge = list(overdue.edges(data=True))[0][2]
    assert edge["urgency"] == data.OVERDUE
    assert edge["color"] == data.URGENCY_STYLE[data.OVERDUE][0]


def test_pressing_commitments_are_drawn_heavier():
    urgent = g.build_graph([make(deadline=NOW)], now=NOW)
    later = g.build_graph([make(deadline=NOW + timedelta(days=40))], now=NOW)
    assert (
        list(urgent.edges(data=True))[0][2]["width"]
        > list(later.edges(data=True))[0][2]["width"]
    )


def test_questions_are_dashed():
    graph = g.build_graph([make(type="question_pending")])
    assert list(graph.edges(data=True))[0][2]["dashes"] is True


def test_edge_tooltip_carries_the_evidence():
    graph = g.build_graph([make(evidence_quote="please send the report by Friday")])
    assert "please send the report by Friday" in list(graph.edges(data=True))[0][2]["title"]


# --- Filtering -------------------------------------------------------------

def test_filtering_by_type():
    graph = g.build_graph(
        [make(id=1, type="meeting"), make(id=2, type="deadline_on_you")],
        types=["meeting"],
    )
    assert graph.number_of_edges() == 1
    assert list(graph.edges(data=True))[0][2]["type"] == "meeting"


def test_filtering_by_person_drops_everyone_else():
    graph = g.build_graph(
        [
            make(id=1, counterparty_email="alice@x.com", counterparty_name="Alice"),
            make(id=2, counterparty_email="bob@x.com", counterparty_name="Bob"),
        ],
        people=["alice@x.com"],
    )
    assert set(graph.nodes) == {g.YOU, "alice@x.com"}


def test_filters_combine():
    graph = g.build_graph(
        [
            make(id=1, type="meeting", counterparty_email="alice@x.com"),
            make(id=2, type="meeting", counterparty_email="bob@x.com"),
            make(id=3, type="deadline_on_you", counterparty_email="alice@x.com"),
        ],
        people=["alice@x.com"],
        types=["meeting"],
    )
    assert graph.number_of_edges() == 1


def test_no_filters_keeps_everything():
    commitments = [make(id=1), make(id=2, counterparty_email="bob@x.com")]
    assert g.build_graph(commitments).number_of_edges() == 2


def test_a_filter_matching_nothing_yields_an_empty_graph():
    graph = g.build_graph([make()], people=["nobody@x.com"])
    assert graph.number_of_nodes() == 0


def test_people_options_are_deduplicated_and_labelled():
    options = g.people_options([
        make(id=1, counterparty_name="Alice", counterparty_email="alice@x.com"),
        make(id=2, counterparty_name="Alice Chen", counterparty_email="alice@x.com"),
        make(id=3, counterparty_name="Bob", counterparty_email="bob@x.com"),
    ])
    assert options == [("alice@x.com", "Alice Chen"), ("bob@x.com", "Bob")]


# --- Stats -----------------------------------------------------------------

def test_stats_split_what_you_owe_from_what_is_owed_to_you():
    stats = g.graph_stats(g.build_graph([
        make(id=1, type="deadline_on_you"),
        make(id=2, type="deadline_on_you"),
        make(id=3, type="deadline_from_others"),
    ]))
    assert stats.people == 1
    assert stats.commitments == 3
    assert stats.you_owe == 2
    assert stats.owed_to_you == 1


def test_busiest_contacts_are_ranked():
    commitments = [make(id=i) for i in range(1, 4)]
    commitments.append(
        make(id=9, counterparty_email="bob@x.com", counterparty_name="Bob")
    )
    stats = g.graph_stats(g.build_graph(commitments))
    assert stats.busiest[0] == ("Alice Chen", 3)
    assert stats.busiest[1] == ("Bob", 1)


# --- Rendering -------------------------------------------------------------

@pytest.fixture(scope="module")
def rendered() -> str:
    graph = g.build_graph(
        [
            make(id=1, type="deadline_on_you"),
            make(id=2, type="deadline_from_others",
                 counterparty_email="bob@x.com", counterparty_name="Bob"),
        ],
        now=NOW,
    )
    return g.render_html(graph)


def test_rendered_html_is_a_working_vis_network(rendered):
    assert "new vis.DataSet" in rendered
    assert "drawGraph" in rendered
    assert "Alice Chen" in rendered


def test_rendered_html_makes_no_external_requests(rendered):
    """The whole point of the project is that nothing phones out (§16).

    pyvis links Bootstrap from a CDN even when told to inline its resources;
    leaving that in would announce app usage and the user's IP to a third party.
    """
    external = re.findall(r'(?:src|href)=["\'](?:https?:)?//[^"\']+', rendered)
    assert external == []


def test_stripping_external_tags_leaves_the_inlined_library_intact(rendered):
    # The vis-network bundle is ~700KB; if the strip had eaten it the page
    # would be tiny and would silently render nothing.
    assert len(rendered) > 100_000
    assert "vis-network" in rendered.lower()


def test_strip_external_resources_removes_link_and_script_tags():
    html = (
        '<link href="https://cdn.example.com/a.css" rel="stylesheet"/>'
        '<script src="https://cdn.example.com/a.js"></script>'
        "<script>var keep = 1;</script>"
    )
    stripped = g.strip_external_resources(html)
    assert "cdn.example.com" not in stripped
    assert "var keep = 1;" in stripped


def test_datetimes_are_serialised_rather_than_crashing_the_render():
    """pyvis JSON-encodes every attribute, so a raw datetime breaks it."""
    graph = g.build_graph([make(deadline=datetime(2026, 8, 15, 14, 30))])
    assert isinstance(list(graph.edges(data=True))[0][2]["deadline"], datetime)

    safe = g._json_safe(graph)
    assert list(safe.edges(data=True))[0][2]["deadline"] == "2026-08-15 14:30"
    # The original is untouched.
    assert isinstance(list(graph.edges(data=True))[0][2]["deadline"], datetime)


def test_an_empty_graph_still_renders():
    assert "drawGraph" in g.render_html(g.build_graph([]))
