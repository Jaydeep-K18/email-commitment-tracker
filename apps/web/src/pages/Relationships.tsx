import type { RelationshipPerson } from "@commitmail/shared";
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";
import { Network } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { Avatar, PageHeader, TierBadge } from "../components/domain";
import { Badge } from "../components/ui/badge";
import { Card, CardBody, CardHeader } from "../components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { cn } from "../lib/cn";
import { deadline } from "../lib/format";
import { useRelationships } from "../lib/queries";

interface Node extends SimulationNodeDatum {
  id: string;
  label: string;
  person?: RelationshipPerson;
  r: number;
}
interface Edge extends SimulationLinkDatum<Node> {
  weight: number;
  direction: "you_owe" | "they_owe";
}

const TIER_FILL: Record<string, string> = {
  CRITICAL: "var(--danger)",
  IMPORTANT: "var(--warning)",
  MONITOR: "var(--info)",
  SKIP: "var(--text-faint)",
  untiered: "var(--text-faint)",
};

const WIDTH = 900;
const HEIGHT = 560;

export default function Relationships() {
  const data = useRelationships();
  const [selected, setSelected] = useState<string | null>(null);
  const people = data.data?.people ?? [];
  const person = people.find((p) => p.key === selected) ?? null;

  return (
    <>
      <PageHeader
        title="Relationships"
        description="Who you're entangled with, and which way it runs. An arrow points at whoever owes."
      />
      {data.isPending ? (
        <Skeleton className="h-[560px] rounded-xl" />
      ) : data.error ? (
        <Card><ErrorState error={data.error} onRetry={() => void data.refetch()} /></Card>
      ) : !people.length ? (
        <Card><EmptyState icon={<Network />} title="No open commitments" description="The graph fills in as the model finds things you owe people — and things they owe you." /></Card>
      ) : (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          <Card className="overflow-hidden">
            <Graph people={people} selected={selected} onSelect={setSelected} />
          </Card>
          <div className="space-y-5">
            <Card>
              <CardHeader title={person ? person.label : "Biggest workload"} description={person ? person.key : "Click a person in the graph"} />
              <CardBody>
                {person ? <PersonDetail person={person} /> : (
                  <ul className="space-y-2">
                    {people.slice(0, 8).map((p) => (
                      <li key={p.key}>
                        <button onClick={() => setSelected(p.key)} className="flex w-full items-center gap-3 rounded-lg p-1.5 text-left hover:bg-surface-2">
                          <Avatar name={p.label} email={p.key} size={30} />
                          <span className="min-w-0 flex-1 truncate text-[13px] text-text">{p.label}</span>
                          <span className="text-[12px] text-muted tabular-nums">{p.youOwe} ↗ · {p.theyOwe} ↙</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Legend" />
              <CardBody className="space-y-2 text-[12.5px] text-muted">
                <p className="flex items-center gap-2"><span className="h-0.5 w-6 bg-accent" /> You owe them</p>
                <p className="flex items-center gap-2"><span className="h-0.5 w-6 bg-success" /> They owe you</p>
                <p>Node colour is the contact's most urgent tier; size is how much is open between you.</p>
              </CardBody>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}

function PersonDetail({ person }: { person: RelationshipPerson }) {
  return (
    <div>
      <div className="flex items-center gap-2">
        <TierBadge tier={person.tier === "untiered" ? null : person.tier} />
        <Badge tone="accent">You owe {person.youOwe}</Badge>
        <Badge tone="success">They owe {person.theyOwe}</Badge>
      </div>
      <ul className="mt-4 space-y-2">
        {person.commitments.map((c) => (
          <li key={c.id}>
            <Link to={`/inbox/${c.emailId}`} className="block rounded-lg border border-border p-2.5 hover:bg-surface-2">
              <p className="text-[13px] font-medium text-text">{c.subject}</p>
              <p className="mt-0.5 text-[12px] text-muted">
                {c.direction === "you_owe" ? "You owe" : "They owe"} · {deadline(c.deadline)}
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Graph({ people, selected, onSelect }: { people: RelationshipPerson[]; selected: string | null; onSelect: (key: string | null) => void }) {
  const [, setTick] = useState(0);
  const [hovered, setHovered] = useState<string | null>(null);
  const svg = useRef<SVGSVGElement>(null);

  const { nodes, edges } = useMemo(() => {
    const you: Node = { id: "__you__", label: "You", r: 22, fx: WIDTH / 2, fy: HEIGHT / 2 };
    const others: Node[] = people.map((p) => ({ id: p.key, label: p.label, person: p, r: 10 + Math.min(14, (p.youOwe + p.theyOwe) * 2.5) }));
    const links: Edge[] = [];
    for (const p of people) {
      if (p.youOwe) links.push({ source: "__you__", target: p.key, weight: p.youOwe, direction: "you_owe" });
      if (p.theyOwe) links.push({ source: p.key, target: "__you__", weight: p.theyOwe, direction: "they_owe" });
    }
    return { nodes: [you, ...others], edges: links };
  }, [people]);

  useEffect(() => {
    const simulation = forceSimulation(nodes)
      .force("link", forceLink<Node, Edge>(edges).id((d) => d.id).distance((l) => 150 - Math.min(60, l.weight * 10)).strength(0.4))
      .force("charge", forceManyBody().strength(-260))
      .force("collide", forceCollide<Node>().radius((d) => d.r + 18))
      .force("x", forceX(WIDTH / 2).strength(0.04))
      .force("y", forceY(HEIGHT / 2).strength(0.06))
      .on("tick", () => setTick((t) => t + 1));
    return () => void simulation.stop();
  }, [nodes, edges]);

  // Drag a person to rearrange; the layout settles around them.
  const drag = (node: Node) => (event: React.PointerEvent) => {
    if (node.id === "__you__" || !svg.current) return;
    const box = svg.current.getBoundingClientRect();
    const toLocal = (e: PointerEvent | React.PointerEvent) => ({
      x: ((e.clientX - box.left) / box.width) * WIDTH,
      y: ((e.clientY - box.top) / box.height) * HEIGHT,
    });
    (event.target as Element).setPointerCapture(event.pointerId);
    const move = (e: PointerEvent) => {
      const p = toLocal(e);
      node.fx = p.x;
      node.fy = p.y;
      setTick((t) => t + 1);
    };
    const up = () => {
      node.fx = null;
      node.fy = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const focus = hovered ?? selected;
  const related = (edge: Edge) => {
    const s = (edge.source as Node).id;
    const t = (edge.target as Node).id;
    return !focus || s === focus || t === focus;
  };

  return (
    <svg ref={svg} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="h-auto w-full touch-none select-none" role="img" aria-label="Relationship graph">
      <defs>
        {(["you_owe", "they_owe"] as const).map((d) => (
          <marker key={d} id={`arrow-${d}`} viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill={d === "you_owe" ? "var(--accent)" : "var(--success)"} />
          </marker>
        ))}
      </defs>
      <g>
        {edges.map((edge, i) => {
          const s = edge.source as Node;
          const t = edge.target as Node;
          if (s.x == null || t.x == null) return null;
          const dx = t.x! - s.x!;
          const dy = t.y! - s.y!;
          const len = Math.hypot(dx, dy) || 1;
          // Stop short of the target circle so the arrowhead is visible, and
          // bow the line slightly so two-way relationships don't overlap.
          const ex = t.x! - (dx / len) * (t.r + 4);
          const ey = t.y! - (dy / len) * (t.r + 4);
          const mx = (s.x! + ex) / 2 - (dy / len) * 14;
          const my = (s.y! + ey) / 2 + (dx / len) * 14;
          return (
            <path
              key={i}
              d={`M${s.x},${s.y} Q${mx},${my} ${ex},${ey}`}
              fill="none"
              stroke={edge.direction === "you_owe" ? "var(--accent)" : "var(--success)"}
              strokeWidth={1 + Math.min(4, edge.weight)}
              strokeOpacity={related(edge) ? 0.75 : 0.1}
              markerEnd={`url(#arrow-${edge.direction})`}
              className="transition-[stroke-opacity] duration-200"
            />
          );
        })}
      </g>
      <g>
        {nodes.map((node) => {
          if (node.x == null) return null;
          const isYou = node.id === "__you__";
          const dim = focus && !isYou && focus !== node.id;
          return (
            <g
              key={node.id}
              transform={`translate(${node.x},${node.y})`}
              onPointerEnter={() => setHovered(node.id)}
              onPointerLeave={() => setHovered(null)}
              onPointerDown={drag(node)}
              onClick={() => !isYou && onSelect(selected === node.id ? null : node.id)}
              className={cn("cursor-pointer transition-opacity duration-200", dim && "opacity-35")}
              role={isYou ? undefined : "button"}
              aria-label={isYou ? undefined : `${node.label}: you owe ${node.person?.youOwe}, they owe ${node.person?.theyOwe}`}
            >
              <circle
                r={node.r}
                fill={isYou ? "var(--text)" : TIER_FILL[node.person?.tier ?? "untiered"]}
                stroke={selected === node.id ? "var(--accent)" : "var(--surface)"}
                strokeWidth={selected === node.id ? 4 : 3}
              />
              <text
                y={node.r + 15}
                textAnchor="middle"
                className="fill-text text-[12px] font-medium"
                style={{ paintOrder: "stroke", stroke: "var(--surface)", strokeWidth: 4 }}
              >
                {node.label.length > 22 ? `${node.label.slice(0, 20)}…` : node.label}
              </text>
              {isYou && <text dy="4" textAnchor="middle" className="fill-canvas text-[11px] font-bold">YOU</text>}
            </g>
          );
        })}
      </g>
    </svg>
  );
}
