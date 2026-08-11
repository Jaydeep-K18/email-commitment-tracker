"""Build the commitment graph: who owes what to whom.

Phase 7 of PROJECT_PLAN.md. Nodes are people, edges are commitments, and the
direction of an edge is the direction of the obligation — an arrow from you to
Alice means you owe Alice something.

The feed answers "what is due next"; this answers "who am I entangled with, and
which way does it run". Someone with six arrows pointing at them is someone you
owe a lot to, which no list sorted by date makes obvious.

Kept free of Streamlit (like :mod:`dashboard.data`) so the graph can be built
and asserted on in tests. ``dashboard/pages/graph.py`` is the thin page on top.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import networkx as nx

from dashboard import data
from src.storage.models import Commitment

#: The node representing the dashboard's owner. Every commitment involves them.
YOU = "__you__"
YOU_LABEL = "You"

#: Counterparty node colour per VIP tier.
TIER_COLOUR = {
    "CRITICAL": "#d92b2b",
    "IMPORTANT": "#e8710a",
    "MONITOR": "#2e6fdb",
    "SKIP": "#9ca3af",
}
UNTIERED_COLOUR = "#6b7280"
YOU_COLOUR = "#111827"

#: Commitment types whose obligation runs *towards* the user.
_INCOMING_TYPES = frozenset({"deadline_from_others", "question_pending"})


def person_key(commitment: Commitment) -> str:
    """Stable identity for the other party.

    Prefers the address: the same person may appear with several display-name
    spellings across emails, and merging them into one node is the whole point.
    """
    if commitment.counterparty_email:
        return commitment.counterparty_email.strip().lower()
    if commitment.counterparty_name:
        return commitment.counterparty_name.strip().lower()
    return "unknown"


def person_label(commitment: Commitment) -> str:
    """Display name for the other party."""
    if commitment.counterparty_name:
        return commitment.counterparty_name.strip()
    if commitment.counterparty_email:
        return commitment.counterparty_email.strip()
    return "Unknown"


def edge_direction(commitment: Commitment) -> tuple[str, str]:
    """``(source, target)`` node keys for one commitment.

    The arrow points the way the obligation runs: towards whoever owes.
    """
    person = person_key(commitment)
    if commitment.type in _INCOMING_TYPES:
        return person, YOU  # they owe you
    return YOU, person       # you owe them (deadlines on you, meetings)


def _edge_tooltip(commitment: Commitment, now: datetime | None = None) -> str:
    when = data.format_deadline(commitment.deadline)
    relative = data.relative_deadline(commitment.deadline, now=now)
    lines = [
        f"{data.type_label(commitment.type)}: {commitment.subject}",
        f"{when}" + (f" ({relative})" if relative else ""),
    ]
    if commitment.evidence_quote:
        lines.append(f"“{commitment.evidence_quote.strip()[:140]}”")
    return "\n".join(lines)


def _node_tooltip(label: str, outgoing: int, incoming: int, tier: str) -> str:
    return "\n".join([
        label,
        f"tier: {tier}",
        f"you owe them: {outgoing}",
        f"they owe you: {incoming}",
    ])


@dataclass
class GraphStats:
    """Summary of what the graph contains."""

    people: int = 0
    commitments: int = 0
    you_owe: int = 0
    owed_to_you: int = 0
    #: ``(label, count)`` ordered by total commitments, busiest first.
    busiest: list[tuple[str, int]] = field(default_factory=list)


def build_graph(
    commitments: list[Commitment],
    *,
    people: list[str] | None = None,
    types: list[str] | None = None,
    now: datetime | None = None,
) -> nx.MultiDiGraph:
    """Build the commitment network.

    A ``MultiDiGraph`` because two people can have several commitments running
    the same way between them, and collapsing those into one edge would hide
    exactly the workload the graph exists to show.

    ``people`` and ``types`` are the plan's two filters; an empty or absent
    filter means "everything".
    """
    selected = [
        c
        for c in commitments
        if (not types or c.type in types) and (not people or person_key(c) in people)
    ]

    graph = nx.MultiDiGraph()

    if not selected:
        return graph

    # Tally first so node size and colour can reflect the whole relationship.
    per_person: dict[str, dict] = {}
    for commitment in selected:
        key = person_key(commitment)
        entry = per_person.setdefault(
            key, {"label": person_label(commitment), "out": 0, "in": 0, "tiers": []}
        )
        # A later email may carry a fuller name than the first one did.
        if len(person_label(commitment)) > len(entry["label"]):
            entry["label"] = person_label(commitment)
        if commitment.type in _INCOMING_TYPES:
            entry["in"] += 1
        else:
            entry["out"] += 1
        if commitment.vip_tier:
            entry["tiers"].append(commitment.vip_tier)

    graph.add_node(
        YOU,
        label=YOU_LABEL,
        title="You",
        color=YOU_COLOUR,
        size=32,
        shape="star",
        is_user=True,
    )

    for key, entry in per_person.items():
        total = entry["out"] + entry["in"]
        tier = _dominant_tier(entry["tiers"])
        graph.add_node(
            key,
            label=entry["label"],
            title=_node_tooltip(entry["label"], entry["out"], entry["in"], tier),
            color=TIER_COLOUR.get(tier, UNTIERED_COLOUR),
            # Grows with the relationship but flattens out, so one very busy
            # contact cannot dwarf everyone else off the canvas.
            size=14 + min(total, 12) * 2,
            tier=tier,
            commitments=total,
            is_user=False,
        )

    for commitment in selected:
        source, target = edge_direction(commitment)
        band = data.urgency(commitment.deadline, now=now)
        colour, _ = data.URGENCY_STYLE[band]
        graph.add_edge(
            source,
            target,
            key=commitment.id,
            commitment_id=commitment.id,
            type=commitment.type,
            subject=commitment.subject,
            deadline=commitment.deadline,
            urgency=band,
            color=colour,
            title=_edge_tooltip(commitment, now=now),
            # Overdue obligations should read as heavier at a glance.
            width=4 if band in (data.OVERDUE, data.TODAY) else 2,
            dashes=commitment.type == "question_pending",
        )

    return graph


def _dominant_tier(tiers: list[str]) -> str:
    """The most urgent tier seen for a person."""
    for tier in ("CRITICAL", "IMPORTANT", "MONITOR", "SKIP"):
        if tier in tiers:
            return tier
    return "untiered"


def graph_stats(graph: nx.MultiDiGraph) -> GraphStats:
    """Summarise a built graph for the page header."""
    stats = GraphStats()
    if graph.number_of_nodes() == 0:
        return stats

    stats.people = graph.number_of_nodes() - 1  # everyone except You
    stats.commitments = graph.number_of_edges()
    stats.you_owe = sum(1 for source, _, _ in graph.edges(keys=True) if source == YOU)
    stats.owed_to_you = stats.commitments - stats.you_owe

    counts = [
        (graph.nodes[node]["label"], graph.nodes[node].get("commitments", 0))
        for node in graph.nodes
        if node != YOU
    ]
    stats.busiest = sorted(counts, key=lambda pair: -pair[1])[:5]
    return stats


def people_options(commitments: list[Commitment]) -> list[tuple[str, str]]:
    """``(key, label)`` for every person present, for the page's filter."""
    seen: dict[str, str] = {}
    for commitment in commitments:
        key = person_key(commitment)
        label = person_label(commitment)
        if key not in seen or len(label) > len(seen[key]):
            seen[key] = label
    return sorted(seen.items(), key=lambda pair: pair[1].lower())


# --- Rendering ------------------------------------------------------------

#: pyvis emits Bootstrap <link>/<script> tags pointing at a public CDN even when
#: told to inline its resources. Left in, opening this page would announce to a
#: third party that the app is in use, from the user's IP — against the whole
#: premise of the project (§16). Bootstrap is only needed for pyvis's own
#: select/filter menus, which are switched off here in favour of Streamlit
#: filters, so removing it costs nothing.
_EXTERNAL_TAG_RE = re.compile(
    r"<(link|script)\b[^>]*?(?:https?:)?//[^>]*?>(?:\s*</script>)?",
    re.IGNORECASE | re.DOTALL,
)

_PHYSICS_OPTIONS = """
{
  "physics": {
    "forceAtlas2Based": {
      "gravitationalConstant": -60,
      "centralGravity": 0.012,
      "springLength": 130,
      "springConstant": 0.09,
      "damping": 0.5
    },
    "minVelocity": 0.75,
    "solver": "forceAtlas2Based",
    "stabilization": {"iterations": 180}
  },
  "edges": {
    "arrows": {"to": {"enabled": true, "scaleFactor": 0.6}},
    "smooth": {"type": "curvedCW", "roundness": 0.15},
    "font": {"size": 0}
  },
  "nodes": {
    "font": {"size": 16, "face": "sans-serif"},
    "borderWidth": 2
  },
  "interaction": {"hover": true, "tooltipDelay": 120, "navigationButtons": true}
}
"""


def strip_external_resources(html: str) -> str:
    """Remove tags that would fetch anything over the network."""
    return _EXTERNAL_TAG_RE.sub("", html)


def _json_safe(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Copy the graph with every attribute value JSON-serializable.

    pyvis serialises *all* node and edge attributes into the page, so a raw
    ``datetime`` on an edge kills the render. Converting here rather than in
    :func:`build_graph` keeps the graph itself a faithful data structure for
    tests and any later analysis.
    """
    safe = graph.copy()
    for _, attrs in safe.nodes(data=True):
        _coerce(attrs)
    for _, _, attrs in safe.edges(data=True):
        _coerce(attrs)
    return safe


def _coerce(attrs: dict) -> None:
    for name, value in list(attrs.items()):
        if isinstance(value, datetime):
            attrs[name] = value.isoformat(sep=" ", timespec="minutes")


#: Canvas colours per theme. The graph renders inside an iframe, so it cannot
#: inherit the dashboard's CSS variables and has to be told which mode it is in
#: — otherwise it stays a glaring white card in an otherwise dark page.
_CANVAS = {
    "light": {
        "bg": "#ffffff", "font": "#111827", "you": YOU_COLOUR,
        "border": "#e4e9f0",
    },
    # "You" is near-black in light mode, which would disappear against a dark
    # canvas, so the centre node inverts along with the background.
    "dark": {
        "bg": "#0f1626", "font": "#e7edf9", "you": "#f2f6ff",
        "border": "#25314b",
    },
}

#: pyvis hardcodes ``border: 1px solid lightgray`` around the canvas, which is a
#: bright line against a dark background. It is not configurable, so it is
#: rewritten after generation.
_CANVAS_BORDER_RE = re.compile(r"border:\s*1px\s+solid\s+lightgray", re.IGNORECASE)


def render_html(
    graph: nx.MultiDiGraph, height: int = 620, theme: str = "light"
) -> str:
    """Render the graph as self-contained, offline-safe HTML."""
    from pyvis.network import Network

    canvas = _CANVAS.get(theme, _CANVAS["light"])

    net = Network(
        height=f"{height}px",
        width="100%",
        directed=True,
        bgcolor=canvas["bg"],
        font_color=canvas["font"],
        # Inlines vis-network rather than linking it from a CDN.
        cdn_resources="in_line",
        notebook=False,
    )
    prepared = _json_safe(graph)
    if YOU in prepared:
        prepared.nodes[YOU]["color"] = canvas["you"]
    net.from_nx(prepared)
    net.set_options(_PHYSICS_OPTIONS)
    html = strip_external_resources(net.generate_html(notebook=False))
    return _CANVAS_BORDER_RE.sub(f"border: 1px solid {canvas['border']}", html)
