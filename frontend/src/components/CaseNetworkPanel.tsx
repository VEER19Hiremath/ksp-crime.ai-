"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchNetworkGraph, type NetworkGraph, type NetworkNode } from "@/lib/api";

const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), { ssr: false });

const TYPE_COLOR: Record<string, string> = {
  Accused: "#C45C26",
  Case: "#0B1F3A",
  Officer: "#2F6F4E",
  Victim: "#5C6B7A",
  Unit: "#B08900",
};

export type CaseRow = Record<string, unknown>;

function pickName(rows: CaseRow[]): string | null {
  for (const row of rows) {
    for (const key of ["person_name", "accused_name", "victim_name", "officer_name", "name"]) {
      const v = row[key];
      if (typeof v === "string" && v.trim().length >= 3 && !/^\d{10,}$/.test(v.trim())) {
        return v.trim();
      }
    }
  }
  return null;
}

function pickCrimeNos(rows: CaseRow[]): string[] {
  const out: string[] = [];
  for (const row of rows) {
    const v = row.crime_no ?? row.case_no;
    if (typeof v === "string" || typeof v === "number") {
      const s = String(v).trim();
      if (s.length >= 8 && !out.includes(s)) out.push(s);
    }
  }
  return out.slice(0, 12);
}

type Props = {
  rows: CaseRow[];
  languageCode?: "en-IN" | "kn-IN";
};

export default function CaseNetworkPanel({ rows, languageCode = "en-IN" }: Props) {
  const kn = languageCode === "kn-IN";
  const [graph, setGraph] = useState<NetworkGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<NetworkNode | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ w: 320, h: 220 });

  const name = useMemo(() => pickName(rows), [rows]);
  const crimeNos = useMemo(() => pickCrimeNos(rows), [rows]);
  const canLoadGraph = Boolean(name || crimeNos.length);
  const graphKey = name || crimeNos.join(",");

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const rect = el.getBoundingClientRect();
      setSize({ w: Math.max(240, Math.floor(rect.width)), h: 220 });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [canLoadGraph]);

  useEffect(() => {
    if (!canLoadGraph) {
      setGraph(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setSelected(null);
    fetchNetworkGraph(name ? { name } : { crime_nos: crimeNos })
      .then((data) => {
        if (!cancelled) setGraph(data);
      })
      .catch(() => {
        if (!cancelled) setGraph({ nodes: [], links: [] });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [canLoadGraph, graphKey, name, crimeNos]);

  if (!rows.length) return null;

  const fgData = graph
    ? {
        nodes: graph.nodes.map((n) => ({ ...n })),
        links: graph.links.map((l) => ({
          source: l.source,
          target: l.target,
          type: l.type,
        })),
      }
    : { nodes: [], links: [] };

  const graphLabel = name || (crimeNos[0] ? `Crime ${crimeNos[0]}${crimeNos.length > 1 ? ` +${crimeNos.length - 1}` : ""}` : "");
  const fullHref = name
    ? `/network?name=${encodeURIComponent(name)}`
    : crimeNos[0]
      ? `/network?crime_no=${encodeURIComponent(crimeNos[0])}`
      : "/network";

  return (
    <div className="mt-3 space-y-3 rounded-xl border border-[var(--border)] bg-[var(--sand)]/40 p-3">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
          {kn ? "ಸಂಬಂಧಿತ ಪ್ರಕರಣಗಳು" : "Related cases"}
        </p>
        <ul className="mt-2 space-y-1.5">
          {rows.slice(0, 8).map((row, i) => {
            const station = String(row.unit_name || row.police_station || "—");
            const crime = String(row.crime_head_name || row.crime_type || "");
            const group = String(row.crime_group_name || "");
            const person = String(row.person_name || row.accused_name || row.officer_name || "");
            const role = row.role ? String(row.role) : "";
            const facts = String(row.brief_facts || "").slice(0, 120);
            const crimeNo = String(row.crime_no || row.case_no || "");
            const status = String(row.case_status_name || "");
            return (
              <li
                key={`${crimeNo}-${i}`}
                className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 py-2 text-xs text-[var(--navy)]"
              >
                <span className="font-medium">
                  {i + 1}. {station}
                  {crime ? ` · ${crime}` : ""}
                  {group ? ` (${group})` : ""}
                </span>
                {person && (
                  <span className="text-[var(--muted)]">
                    {" "}
                    — {person}
                    {role ? ` (${role})` : ""}
                  </span>
                )}
                {status && <span className="text-[var(--muted)]"> · {status}</span>}
                {facts && <p className="mt-0.5 text-[var(--muted)]">{facts}</p>}
              </li>
            );
          })}
        </ul>
      </div>

      {canLoadGraph && (
        <div>
          <div className="mb-1 flex items-center justify-between gap-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
              {kn ? "ನೆಟ್‌ವರ್ಕ್" : "Network"} · {graphLabel}
            </p>
            <a
              href={fullHref}
              className="text-[10px] font-medium text-[var(--saffron)] hover:underline"
            >
              {kn ? "ಪೂರ್ಣ ವೀಕ್ಷಣೆ" : "Open full graph"}
            </a>
          </div>
          <div
            ref={wrapRef}
            className="relative h-[220px] overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface)]"
          >
            {loading && (
              <p className="absolute inset-0 flex items-center justify-center text-xs text-[var(--muted)]">
                {kn ? "ನೆಟ್‌ವರ್ಕ್ ಲೋಡ್…" : "Loading network…"}
              </p>
            )}
            {!loading && graph && graph.nodes.length === 0 && (
              <p className="absolute inset-0 flex items-center justify-center text-xs text-[var(--muted)]">
                {kn ? "ನೆಟ್‌ವರ್ಕ್ ಲಿಂಕ್ ಸಿಗಲಿಲ್ಲ." : "No network links for these cases."}
              </p>
            )}
            {!loading && graph && graph.nodes.length > 0 && size.w >= 200 && (
              <ForceGraph2D
                graphData={fgData}
                width={size.w}
                height={size.h}
                nodeId="id"
                backgroundColor="rgba(0,0,0,0)"
                nodeLabel={(n) => `${(n as NetworkNode).type}: ${(n as NetworkNode).label}`}
                nodeCanvasObject={(node, ctx, globalScale) => {
                  const n = node as NetworkNode & { x?: number; y?: number };
                  const color = TYPE_COLOR[n.type] || "#5C6B7A";
                  const r = n.type === "Case" ? 4 : 5;
                  ctx.beginPath();
                  ctx.arc(n.x || 0, n.y || 0, r, 0, 2 * Math.PI);
                  ctx.fillStyle = color;
                  ctx.fill();
                  if (globalScale > 1.05) {
                    ctx.font = `${10 / globalScale}px sans-serif`;
                    ctx.fillStyle = "#0B1F3A";
                    ctx.fillText(n.label.slice(0, 18), (n.x || 0) + 5, (n.y || 0) + 3);
                  }
                }}
                linkColor={() => "rgba(11,31,58,0.35)"}
                cooldownTicks={60}
                enableNodeDrag
                onNodeClick={(node) => setSelected(node as NetworkNode)}
              />
            )}
          </div>
          {selected && (
            <div className="mt-2 rounded-md border border-[var(--border)] bg-[var(--surface)] p-2 text-[11px] text-[var(--navy)]">
              <p className="font-medium">
                {selected.type}: {selected.label}
              </p>
              <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[var(--muted)]">
                {Object.entries(selected.props || {}).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="font-medium text-[var(--navy)]/70">{k}</dt>
                    <dd className="truncate">{String(v)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
