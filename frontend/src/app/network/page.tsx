"use client";

import dynamic from "next/dynamic";
import { FormEvent, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  fetchNetworkGraph,
  fetchNetworkSuggestions,
  type NetworkGraph,
  type NetworkNode,
} from "@/lib/api";
import AppShell from "@/components/AppShell";

const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), { ssr: false });

const TYPE_COLOR: Record<string, string> = {
  Accused: "#C45C26",
  Case: "#0B1F3A",
  Officer: "#2F6F4E",
  Victim: "#5C6B7A",
  Unit: "#B08900",
};

function NetworkContent() {
  const searchParams = useSearchParams();
  const [graph, setGraph] = useState<NetworkGraph | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<NetworkNode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [hints, setHints] = useState<string[]>([]);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ w: 640, h: 420 });

  const load = useCallback(async (name?: string) => {
    setLoading(true);
    setError(null);
    setSelected(null);
    try {
      const data = await fetchNetworkGraph(name ? { name } : undefined);
      setGraph(data);
      if (data.error) setError(data.error);
    } catch {
      setError("Could not load the criminal network. Is the backend running?");
      setGraph({ nodes: [], links: [] });
    } finally {
      setLoading(false);
    }
  }, []);

  const loadByCrime = useCallback(async (crimeNo: string) => {
    setLoading(true);
    setError(null);
    setSelected(null);
    try {
      const data = await fetchNetworkGraph({ crime_no: crimeNo });
      setGraph(data);
      if (data.error) setError(data.error);
    } catch {
      setError("Could not load the criminal network. Is the backend running?");
      setGraph({ nodes: [], links: [] });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const name = searchParams.get("name")?.trim() || "";
    const crimeNo = searchParams.get("crime_no")?.trim() || "";
    if (name) {
      setQuery(name);
      void load(name);
    } else if (crimeNo) {
      setQuery(crimeNo);
      void loadByCrime(crimeNo);
    } else {
      void load();
    }
  }, [load, loadByCrime, searchParams]);

  useEffect(() => {
    void fetchNetworkSuggestions(10)
      .then((d) => setHints(d.names || []))
      .catch(() => setHints([]));
  }, []);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const rect = el.getBoundingClientRect();
      setSize({ w: Math.max(320, Math.floor(rect.width)), h: Math.max(360, Math.floor(rect.height)) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  function handleSearch(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (/^\d{8,}$/.test(q)) {
      void loadByCrime(q);
    } else {
      void load(q || undefined);
    }
  }

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

  return (
    <main className="mx-auto flex h-full max-w-6xl flex-col p-4 md:p-6">
      <header className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display text-2xl text-[var(--navy)]">Criminal network</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Case–person–officer links built from live Postgres records (crime type, station, status, facts).
          </p>
          {hints.length > 0 && (
            <p className="mt-1 text-xs text-[var(--muted)]">
              Try:{" "}
              {hints.slice(0, 5).map((n, i) => (
                <button
                  key={n}
                  type="button"
                  className="text-[var(--saffron)] hover:underline"
                  onClick={() => {
                    setQuery(n);
                    void load(n);
                  }}
                >
                  {n}
                  {i < Math.min(4, hints.length - 1) ? ", " : ""}
                </button>
              ))}
            </p>
          )}
        </div>
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search person, officer, or crime no…"
            className="min-w-[200px] rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--navy)] placeholder:text-[var(--muted)]"
          />
          <button
            type="submit"
            className="rounded-md bg-[var(--navy)] px-4 py-2 text-sm text-white hover:bg-[var(--navy-deep)]"
          >
            Search
          </button>
          <button
            type="button"
            onClick={() => {
              setQuery("");
              load();
            }}
            className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--navy)]"
          >
            Reset
          </button>
        </form>
      </header>

      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
      {loading && <p className="mb-3 text-sm text-[var(--muted)]">Loading network…</p>}

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1fr_280px]">
        <div
          ref={wrapRef}
          className="relative min-h-[420px] overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--surface)]"
        >
          {!loading && graph && graph.nodes.length === 0 && (
            <p className="absolute inset-0 flex items-center justify-center text-sm text-[var(--muted)]">
              No network edges found. Try another name from the suggestions above.
            </p>
          )}
          {graph && graph.nodes.length > 0 && size.w >= 200 && (
            <ForceGraph2D
              graphData={fgData}
              width={size.w}
              height={size.h}
              nodeId="id"
              backgroundColor="rgba(0,0,0,0)"
              nodeLabel={(n) => `${(n as NetworkNode).type}: ${(n as NetworkNode).label}`}
              nodeCanvasObject={(node, ctx, globalScale) => {
                const n = node as NetworkNode & { x?: number; y?: number };
                const label = n.label;
                const color = TYPE_COLOR[n.type] || "#5C6B7A";
                const r = n.type === "Case" ? 5 : 7;
                ctx.beginPath();
                ctx.arc(n.x ?? 0, n.y ?? 0, r, 0, 2 * Math.PI);
                ctx.fillStyle = color;
                ctx.fill();
                if (globalScale > 1.2) {
                  ctx.font = `${12 / globalScale}px IBM Plex Sans, sans-serif`;
                  ctx.fillStyle = "#0B1F3A";
                  ctx.fillText(label, (n.x ?? 0) + 8, (n.y ?? 0) + 3);
                }
              }}
              linkColor={() => "#c4b9a8"}
              linkWidth={1.2}
              onNodeClick={(node) => setSelected(node as NetworkNode)}
              cooldownTicks={80}
            />
          )}
        </div>

        <aside className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4 text-sm">
          <h2 className="font-display text-base text-[var(--navy)]">Legend</h2>
          <ul className="mt-2 space-y-1.5">
            {Object.entries(TYPE_COLOR).map(([type, color]) => (
              <li key={type} className="flex items-center gap-2 text-[var(--muted)]">
                <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: color }} />
                {type}
              </li>
            ))}
          </ul>
          <div className="mt-4 border-t border-[var(--border)] pt-3">
            <h3 className="font-medium text-[var(--navy)]">Properties</h3>
            {!selected && <p className="mt-1 text-[var(--muted)]">Click a node for live DB details.</p>}
            {selected && (
              <div className="mt-2 space-y-1">
                <p>
                  <span className="text-[var(--muted)]">Type:</span> {selected.type}
                </p>
                <p>
                  <span className="text-[var(--muted)]">Label:</span> {selected.label}
                </p>
                <dl className="mt-2 max-h-64 space-y-1 overflow-auto rounded bg-[var(--sand)] p-2 text-xs text-[var(--navy)]">
                  {Object.entries(selected.props || {}).map(([k, v]) => (
                    <div key={k}>
                      <dt className="font-medium text-[var(--muted)]">{k}</dt>
                      <dd className="whitespace-pre-wrap break-words">{String(v)}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </div>
          {graph && (
            <p className="mt-4 text-xs text-[var(--muted)]">
              {graph.nodes.length} nodes · {graph.links.length} links
              {graph.source ? ` · ${graph.source}` : ""}
            </p>
          )}
        </aside>
      </div>
    </main>
  );
}

export default function NetworkPage() {
  return (
    <AppShell>
      <Suspense fallback={<p className="p-6 text-sm text-[var(--muted)]">Loading network…</p>}>
        <NetworkContent />
      </Suspense>
    </AppShell>
  );
}
