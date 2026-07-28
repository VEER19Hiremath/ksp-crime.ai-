"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  fetchCrimePatterns,
  fetchDashboardHotspots,
  fetchDashboardSocio,
  fetchDashboardSummary,
  fetchDashboardTrend,
  fetchEarlyWarnings,
  type Hotspot,
} from "@/lib/api";
import AppShell from "@/components/AppShell";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });
const CrimeMap = dynamic(() => import("@/components/CrimeMap"), { ssr: false });

type MetaRow = { label: string; value: string };
type InsightDetail = {
  id: string;
  title: string;
  subtitle: string;
  meta: MetaRow[];
  rows?: { heading: string; cells: string[] }[];
  questions: string[];
};

type Warning = Awaited<ReturnType<typeof fetchEarlyWarnings>>["warnings"][number];
type Pattern = Awaited<ReturnType<typeof fetchCrimePatterns>>["patterns"][number];

function askInChat(question: string) {
  sessionStorage.setItem("crimeai_pending_prompt", question);
  const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
  window.location.href = `${base}/?q=${encodeURIComponent(question)}`;
}

function sectionClass(active: boolean) {
  return `rounded-md border p-3 transition ${
    active
      ? "border-[var(--saffron)] bg-[var(--surface)] ring-1 ring-[var(--saffron)]/40"
      : "border-[var(--border)] bg-[var(--surface)] hover:border-[var(--navy)]/30"
  }`;
}

function InsightPanel({
  detail,
  onClose,
}: {
  detail: InsightDetail;
  onClose: () => void;
}) {
  return (
    <aside className="sticky top-4 rounded-lg border border-[var(--navy)]/20 bg-[var(--surface)] p-4 shadow-sm">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--saffron)]">
            Insight detail
          </p>
          <h2 className="font-display text-xl text-[var(--navy)]">{detail.title}</h2>
          <p className="mt-1 text-sm text-[var(--muted)]">{detail.subtitle}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 text-xs text-[var(--muted)] underline underline-offset-2 hover:text-[var(--navy)]"
        >
          Close
        </button>
      </div>

      <div className="mb-4 grid gap-2 sm:grid-cols-2">
        {detail.meta.map((m) => (
          <div
            key={`${m.label}-${m.value}`}
            className="rounded-md border border-[var(--border)] bg-[var(--sand)]/60 px-3 py-2"
          >
            <p className="text-[10px] uppercase tracking-wide text-[var(--muted)]">{m.label}</p>
            <p className="text-sm font-medium text-[var(--navy)]">{m.value}</p>
          </div>
        ))}
      </div>

      {detail.rows && detail.rows.length > 0 && (
        <div className="mb-4">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Supporting data
          </p>
          <ul className="max-h-52 space-y-1.5 overflow-y-auto text-sm">
            {detail.rows.map((row, i) => (
              <li
                key={`${row.heading}-${i}`}
                className="rounded-md border border-[var(--border)] px-3 py-2"
              >
                <p className="font-medium text-[var(--navy)]">{row.heading}</p>
                <p className="text-xs text-[var(--muted)]">{row.cells.join(" · ")}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
          Ask about this
        </p>
        <div className="flex flex-wrap gap-2">
          {detail.questions.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => askInChat(q)}
              className="rounded-md border border-[var(--navy)]/15 bg-[var(--sand)] px-3 py-1.5 text-left text-xs text-[var(--navy)] hover:border-[var(--saffron)] hover:bg-white"
            >
              {q}
            </button>
          ))}
        </div>
        <p className="mt-3 text-xs text-[var(--muted)]">
          Opens Chat with this question so you can keep investigating from the live data.
        </p>
      </div>
    </aside>
  );
}

function DashboardContent() {
  const router = useRouter();
  const [summary, setSummary] = useState<Record<string, number> | null>(null);
  const [trend, setTrend] = useState<{ month: string; crime_group_name: string; count: number }[]>([]);
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [socio, setSocio] = useState<Awaited<ReturnType<typeof fetchDashboardSocio>> | null>(null);
  const [warnings, setWarnings] = useState<Warning[]>([]);
  const [patterns, setPatterns] = useState<Pattern[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<InsightDetail | null>(null);

  useEffect(() => {
    let cancelled = false;
    let pending = 6;
    setLoading(true);
    setError(null);

    const doneOne = () => {
      pending -= 1;
      if (pending <= 0 && !cancelled) setLoading(false);
    };

    const failAllCheck = () => {
      // If everything failed and we still have no summary, show an error.
      if (!cancelled && pending <= 0) {
        setSummary((current) => {
          if (current == null) {
            setError("Could not reach the backend. Is the API server running?");
          }
          return current;
        });
      }
    };

    fetchDashboardSummary()
      .then((s) => {
        if (!cancelled) {
          setSummary(s);
          setLoading(false); // paint KPI cards immediately
        }
      })
      .catch((err) => {
        if (
          !cancelled &&
          err instanceof Error &&
          /failed:\s*401/.test(err.message)
        ) {
          setError("Session expired — please sign in again.");
        }
      })
      .finally(() => {
        doneOne();
        failAllCheck();
      });

    fetchDashboardTrend()
      .then((t) => {
        if (!cancelled) setTrend(t);
      })
      .catch(() => undefined)
      .finally(doneOne);

    fetchDashboardHotspots(1)
      .then((h) => {
        if (!cancelled) setHotspots(h);
      })
      .catch(() => undefined)
      .finally(doneOne);

    fetchDashboardSocio()
      .then((so) => {
        if (!cancelled) setSocio(so);
      })
      .catch(() => undefined)
      .finally(doneOne);

    fetchEarlyWarnings(8)
      .then((w) => {
        if (!cancelled) setWarnings(w.warnings || []);
      })
      .catch(() => undefined)
      .finally(doneOne);

    fetchCrimePatterns(10)
      .then((p) => {
        if (!cancelled) setPatterns(p.patterns || []);
      })
      .catch(() => undefined)
      .finally(doneOne);

    return () => {
      cancelled = true;
    };
  }, []);

  const totalCases = summary ? Object.values(summary).reduce((a, b) => a + b, 0) : 0;

  const chartOption = useMemo(() => {
    const months = [...new Set(trend.map((r) => r.month))].sort();
    const groups = [...new Set(trend.map((r) => r.crime_group_name))];
    const series = groups.map((group) => ({
      name: group,
      type: "line" as const,
      smooth: true,
      showSymbol: false,
      data: months.map((m) => {
        const row = trend.find((r) => r.month === m && r.crime_group_name === group);
        return row?.count ?? 0;
      }),
    }));
    return {
      color: ["#C45C26", "#0B1F3A", "#2F6F4E", "#B08900", "#5C6B7A"],
      tooltip: { trigger: "axis" },
      legend: { top: 0, type: "scroll", textStyle: { color: "#3d4a5c" } },
      grid: { left: 40, right: 16, top: 48, bottom: 32 },
      xAxis: {
        type: "category",
        data: months,
        axisLabel: { color: "#5c6b7a", rotate: months.length > 8 ? 35 : 0 },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        axisLabel: { color: "#5c6b7a" },
        splitLine: { lineStyle: { color: "#e8e2d6" } },
      },
      series,
    };
  }, [trend]);

  const accusedAgeOption = useMemo(() => {
    const bands = socio?.accused_age_bands || [];
    return {
      color: ["#C45C26"],
      tooltip: { trigger: "axis" },
      grid: { left: 40, right: 12, top: 24, bottom: 28 },
      xAxis: { type: "category", data: bands.map((b) => b.age_band), axisLabel: { color: "#5c6b7a" } },
      yAxis: { type: "value", minInterval: 1, axisLabel: { color: "#5c6b7a" } },
      series: [{ type: "bar", data: bands.map((b) => b.count), name: "Accused" }],
    };
  }, [socio]);

  function openSummary() {
    if (!summary) return;
    setSelected({
      id: "summary",
      title: "Case status overview",
      subtitle: "Live FIR counts from the SCRB case master.",
      meta: [
        { label: "Total FIRs", value: String(totalCases) },
        ...Object.entries(summary).map(([status, count]) => ({
          label: status,
          value: String(count),
        })),
      ],
      rows: Object.entries(summary).map(([status, count]) => ({
        heading: status,
        cells: [`${count} cases`, `${totalCases ? Math.round((count / totalCases) * 100) : 0}% of total`],
      })),
      questions: [
        "How many cases are under investigation?",
        "How many charge sheeted cases are there?",
        "Which station has the most cases?",
        "Show crime trends and early warnings",
      ],
    });
  }

  function openTrends() {
    const months = [...new Set(trend.map((r) => r.month))].sort();
    const latest = months[months.length - 1] || "";
    const latestRows = trend.filter((r) => r.month === latest);
    setSelected({
      id: "trends",
      title: "Crime trends",
      subtitle: "Monthly case counts by crime group from live Postgres.",
      meta: [
        { label: "Months covered", value: String(months.length) },
        { label: "Crime groups", value: String(new Set(trend.map((r) => r.crime_group_name)).size) },
        { label: "Latest month", value: latest || "—" },
        {
          label: "Latest month total",
          value: String(latestRows.reduce((s, r) => s + Number(r.count || 0), 0)),
        },
      ],
      rows: latestRows.map((r) => ({
        heading: r.crime_group_name,
        cells: [r.month, `${r.count} cases`],
      })),
      questions: [
        "Show crime trends",
        "Which crime type has the most cases?",
        "How many cases this year?",
        "Show early warnings",
      ],
    });
  }

  function openHotspots() {
    const sorted = [...hotspots].sort((a, b) => Number(b.case_count) - Number(a.case_count));
    const top = sorted[0];
    setSelected({
      id: "hotspots",
      title: "Crime hotspots",
      subtitle: "Geo-tagged case density by police station.",
      meta: [
        { label: "Mapped locations", value: String(hotspots.length) },
        { label: "Top station", value: top?.unit_name || "—" },
        { label: "Top district", value: top?.district_name || "—" },
        { label: "Top case count", value: top ? String(top.case_count) : "—" },
      ],
      rows: sorted.slice(0, 8).map((h) => ({
        heading: h.unit_name || "Location",
        cells: [
          h.district_name || "Unknown district",
          `${h.case_count} cases`,
          `${Number(h.lat_bucket).toFixed(2)}, ${Number(h.lng_bucket).toFixed(2)}`,
        ],
      })),
      questions: [
        top?.unit_name ? `Show cases at ${top.unit_name}` : "Which station has the most cases?",
        top?.district_name ? `How many cases in ${top.district_name}?` : "Show crime hotspots",
        "Which station has the most cases?",
        "Show robbery cases in Hubballi",
      ],
    });
  }

  function openHotspotStation(h: Hotspot) {
    setSelected({
      id: `hotspot-${h.unit_name || h.lat_bucket}`,
      title: h.unit_name || "Hotspot location",
      subtitle: "Live geo-tagged case density for this station.",
      meta: [
        { label: "Police station", value: h.unit_name || "—" },
        { label: "District", value: h.district_name || "—" },
        { label: "Case count", value: String(h.case_count) },
        {
          label: "Coordinates",
          value: `${Number(h.lat_bucket).toFixed(4)}, ${Number(h.lng_bucket).toFixed(4)}`,
        },
      ],
      questions: [
        h.unit_name ? `Show cases at ${h.unit_name}` : "Which station has the most cases?",
        h.district_name ? `How many cases in ${h.district_name}?` : "Show crime hotspots",
        h.unit_name ? `Show crime patterns at ${h.unit_name}` : "Show crime patterns",
        "What are the early warnings?",
      ],
    });
  }

  function openSocio() {
    if (!socio) return;
    const topAge = [...(socio.accused_age_bands || [])].sort((a, b) => b.count - a.count)[0];
    const topGender = [...(socio.accused_gender || [])].sort((a, b) => b.count - a.count)[0];
    setSelected({
      id: "socio",
      title: "Socio-demographic insights",
      subtitle: "Age, gender, and occupation signals from accused / complainant records.",
      meta: [
        { label: "Top accused age band", value: topAge ? `${topAge.age_band} (${topAge.count})` : "—" },
        { label: "Top accused gender", value: topGender ? `${topGender.gender} (${topGender.count})` : "—" },
        { label: "Victim age bands", value: String(socio.victim_age_bands?.length || 0) },
        {
          label: "Occupation categories",
          value: String(socio.complainant_occupations?.length || 0),
        },
      ],
      rows: [
        ...(socio.accused_age_bands || []).map((b) => ({
          heading: `Accused age ${b.age_band}`,
          cells: [`${b.count} people`],
        })),
        ...(socio.accused_gender || []).map((g) => ({
          heading: `Accused gender ${g.gender}`,
          cells: [`${g.count} people`],
        })),
        ...(socio.complainant_occupations || []).slice(0, 5).map((o) => ({
          heading: o.occupation_name,
          cells: [`${o.count} complainants`],
        })),
      ],
      questions: [
        "Show socio demographic insights",
        "What is the accused age profile?",
        "Show victim demographics",
        "Which occupations appear most among complainants?",
      ],
    });
  }

  function openWarning(w: Warning, index: number) {
    setSelected({
      id: `warning-${index}`,
      title: `Early warning · ${w.unit_name}`,
      subtitle: `${w.crime_head_name} is rising versus the prior month.`,
      meta: [
        { label: "Police station", value: w.unit_name },
        { label: "District", value: w.district_name || "—" },
        { label: "Crime type", value: w.crime_head_name },
        { label: "Previous month", value: String(w.previous_count) },
        { label: "Current month", value: String(w.current_count) },
        { label: "Increase", value: `+${w.delta}` },
      ],
      rows: [
        {
          heading: "Recommendation",
          cells: [w.recommendation],
        },
      ],
      questions: [
        `Show ${w.crime_head_name.toLowerCase()} cases at ${w.unit_name}`,
        `How many ${w.crime_head_name.toLowerCase()} cases are under investigation?`,
        "What are the early warnings?",
        `Show crime patterns at ${w.unit_name}`,
      ],
    });
  }

  function openPattern(p: Pattern, index: number) {
    setSelected({
      id: `pattern-${index}`,
      title: p.pattern,
      subtitle: "Recurring crime-type × station cluster from live case history.",
      meta: [
        { label: "Police station", value: p.unit_name },
        { label: "District", value: p.district_name || "—" },
        { label: "Crime type", value: p.crime_head_name },
        { label: "Crime group", value: p.crime_group_name || "—" },
        { label: "Case count", value: String(p.case_count) },
        {
          label: "Active window",
          value: p.first_seen ? `${p.first_seen} → ${p.last_seen}` : "—",
        },
      ],
      questions: [
        `Show ${p.crime_head_name.toLowerCase()} cases at ${p.unit_name}`,
        `Show crime patterns`,
        `How many ${p.crime_head_name.toLowerCase()} cases are there?`,
        p.district_name ? `Show cases in ${p.district_name}` : "Which station has the most cases?",
      ],
    });
  }

  function openWarningsOverview() {
    setSelected({
      id: "warnings",
      title: "Early warnings",
      subtitle: "Stations where crime is rising versus the prior month.",
      meta: [
        { label: "Active warnings", value: String(warnings.length) },
        {
          label: "Largest spike",
          value: warnings[0]
            ? `${warnings[0].unit_name} · ${warnings[0].crime_head_name} (+${warnings[0].delta})`
            : "—",
        },
      ],
      rows: warnings.map((w) => ({
        heading: `${w.unit_name} · ${w.crime_head_name}`,
        cells: [
          w.district_name || "—",
          `${w.previous_count} → ${w.current_count}`,
          `+${w.delta}`,
        ],
      })),
      questions: [
        "What are the early warnings?",
        "Show crime trends",
        "Which station has the most cases?",
        "Show crime patterns",
      ],
    });
  }

  return (
    <main className="h-full overflow-y-auto p-3 md:p-4">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-xl text-[var(--navy)]">Crime overview</h1>
          <p className="text-xs text-[var(--muted)]">
            Click an insight for metadata and related chat questions.
          </p>
        </div>
        <button
          type="button"
          onClick={() => router.push("/")}
          className="rounded-md bg-[var(--navy)] px-3 py-1.5 text-xs text-white hover:bg-[var(--navy-deep)]"
        >
          Open Chat
        </button>
      </header>

      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
      {loading && !summary && (
        <p className="text-sm text-[var(--muted)]">Loading dashboard…</p>
      )}
      {loading && summary && (
        <p className="mb-2 text-xs text-[var(--muted)]">Loading remaining charts…</p>
      )}

      <div
        className={`grid gap-4 ${
          selected ? "lg:grid-cols-[minmax(0,1fr)_320px]" : "grid-cols-1"
        }`}
      >
        <div className="min-w-0 space-y-4">
          {summary && (
            <section>
              <button type="button" onClick={openSummary} className={`w-full text-left ${sectionClass(selected?.id === "summary")}`}>
                <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
                  Case status · click for metadata
                </p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <div className="rounded-md bg-[var(--sand)]/70 p-2.5">
                    <div className="text-xl font-semibold text-[var(--navy)]">{totalCases}</div>
                    <div className="text-[10px] uppercase tracking-wide text-[var(--muted)]">Total FIRs</div>
                  </div>
                  {Object.entries(summary).map(([status, count]) => (
                    <div key={status} className="rounded-md bg-[var(--sand)]/70 p-2.5">
                      <div className="text-xl font-semibold text-[var(--navy)]">{count}</div>
                      <div className="text-[10px] uppercase tracking-wide text-[var(--muted)]">{status}</div>
                    </div>
                  ))}
                </div>
              </button>
            </section>
          )}

          {warnings.length > 0 && (
            <section className={sectionClass(selected?.id === "warnings" || selected?.id.startsWith("warning-") === true)}>
              <button type="button" onClick={openWarningsOverview} className="mb-2 w-full text-left">
                <h2 className="font-display text-base text-[var(--navy)]">Early warnings</h2>
                <p className="text-xs text-[var(--muted)]">
                  Rising spikes — click a row for metadata
                </p>
              </button>
              <ul className="space-y-1.5">
                {warnings.map((w, i) => (
                  <li key={`${w.unit_name}-${w.crime_head_name}-${i}`}>
                    <button
                      type="button"
                      onClick={() => openWarning(w, i)}
                      className={`w-full rounded-md border px-2.5 py-1.5 text-left text-sm transition ${
                        selected?.id === `warning-${i}`
                          ? "border-[var(--saffron)] bg-white"
                          : "border-[var(--border)] bg-[var(--sand)]/50 hover:border-[var(--navy)]/30"
                      }`}
                    >
                      <span className="font-medium text-[var(--navy)]">
                        {w.unit_name} · {w.crime_head_name}
                      </span>
                      <span className="text-[var(--muted)]">
                        {" "}
                        ({w.previous_count} → {w.current_count}, +{w.delta})
                      </span>
                      <p className="mt-0.5 text-xs text-[var(--muted)]">{w.recommendation}</p>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <div
              role="button"
              tabIndex={0}
              onClick={openTrends}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") openTrends();
              }}
              className={`cursor-pointer text-left ${sectionClass(selected?.id === "trends")}`}
            >
              <h2 className="mb-1 font-display text-base text-[var(--navy)]">Crime trends</h2>
              <p className="mb-2 text-xs text-[var(--muted)]">Monthly counts by crime group</p>
              {trend.length > 0 ? (
                <ReactECharts option={chartOption} style={{ height: 240 }} opts={{ renderer: "svg" }} />
              ) : (
                !loading && <p className="text-sm text-[var(--muted)]">No trend data available.</p>
              )}
            </div>

            <div
              role="button"
              tabIndex={0}
              onClick={openHotspots}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") openHotspots();
              }}
              className={`cursor-pointer text-left ${sectionClass(selected?.id === "hotspots")}`}
            >
              <h2 className="mb-1 font-display text-base text-[var(--navy)]">Crime hotspots</h2>
              <p className="mb-2 text-xs text-[var(--muted)]">Case density by police station</p>
              {hotspots.length > 0 ? (
                <div
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.stopPropagation()}
                >
                  <CrimeMap hotspots={hotspots} onSelect={openHotspotStation} />
                </div>
              ) : (
                !loading && <p className="text-sm text-[var(--muted)]">No geo-tagged cases for the map.</p>
              )}
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div
              role="button"
              tabIndex={0}
              onClick={openSocio}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") openSocio();
              }}
              className={`cursor-pointer text-left ${sectionClass(selected?.id === "socio")}`}
            >
              <h2 className="mb-1 font-display text-base text-[var(--navy)]">Socio-demographic insights</h2>
              <p className="mb-2 text-xs text-[var(--muted)]">Accused age bands from live case records</p>
              {socio && socio.accused_age_bands.length > 0 ? (
                <ReactECharts option={accusedAgeOption} style={{ height: 220 }} opts={{ renderer: "svg" }} />
              ) : (
                !loading && <p className="text-sm text-[var(--muted)]">No demographic data.</p>
              )}
            </div>

            <section className={sectionClass(selected?.id.startsWith("pattern-") === true)}>
              <h2 className="mb-1 font-display text-base text-[var(--navy)]">Crime pattern discovery</h2>
              <p className="mb-2 text-xs text-[var(--muted)]">
                Recurring crime × station — click for metadata
              </p>
              {patterns.length > 0 ? (
                <ul className="max-h-[280px] space-y-1.5 overflow-y-auto text-sm">
                  {patterns.map((p, i) => (
                    <li key={`${p.unit_name}-${p.crime_head_name}-${i}`}>
                      <button
                        type="button"
                        onClick={() => openPattern(p, i)}
                        className={`w-full rounded-md border px-2.5 py-1.5 text-left transition ${
                          selected?.id === `pattern-${i}`
                            ? "border-[var(--saffron)] bg-white"
                            : "border-[var(--border)] hover:border-[var(--navy)]/30"
                        }`}
                      >
                        <p className="font-medium text-[var(--navy)]">{p.pattern}</p>
                        <p className="text-xs text-[var(--muted)]">
                          {p.case_count} cases · {p.crime_group_name} · {p.district_name}
                          {p.first_seen ? ` · ${p.first_seen} → ${p.last_seen}` : ""}
                        </p>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                !loading && <p className="text-sm text-[var(--muted)]">No recurring patterns yet.</p>
              )}
            </section>
          </div>
        </div>

        {selected ? <InsightPanel detail={selected} onClose={() => setSelected(null)} /> : null}
      </div>
    </main>
  );
}

export default function DashboardPage() {
  return (
    <AppShell>
      <DashboardContent />
    </AppShell>
  );
}
