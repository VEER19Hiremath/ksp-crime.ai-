"use client";

import { useMemo } from "react";
import { CircleMarker, MapContainer, Popup, TileLayer } from "react-leaflet";
import type { Hotspot } from "@/lib/api";
import "leaflet/dist/leaflet.css";

type Props = {
  hotspots: Hotspot[];
  onSelect?: (hotspot: Hotspot) => void;
};

export default function CrimeMap({ hotspots, onSelect }: Props) {
  const center = useMemo((): [number, number] => {
    if (!hotspots.length) return [15.3173, 75.7139]; // Karnataka centroid approx
    const lat = hotspots.reduce((s, h) => s + Number(h.lat_bucket), 0) / hotspots.length;
    const lng = hotspots.reduce((s, h) => s + Number(h.lng_bucket), 0) / hotspots.length;
    return [lat, lng];
  }, [hotspots]);

  const maxCount = Math.max(...hotspots.map((h) => Number(h.case_count)), 1);

  return (
    <MapContainer
      center={center}
      zoom={7}
      scrollWheelZoom={false}
      className="h-[260px] w-full rounded-md"
      style={{ background: "#e8e2d6" }}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {hotspots.map((h) => {
        const count = Number(h.case_count);
        const radius = 8 + (count / maxCount) * 18;
        return (
          <CircleMarker
            key={`${h.lat_bucket}-${h.lng_bucket}-${h.unit_name || ""}`}
            center={[Number(h.lat_bucket), Number(h.lng_bucket)]}
            radius={radius}
            eventHandlers={
              onSelect
                ? {
                    click: () => onSelect(h),
                  }
                : undefined
            }
            pathOptions={{
              color: "#0B1F3A",
              fillColor: "#C45C26",
              fillOpacity: 0.55,
              weight: 1,
            }}
          >
            <Popup>
              <strong>{h.unit_name ?? "Location"}</strong>
              {h.district_name ? <><br />{h.district_name}</> : null}
              <br />
              {count} case{count === 1 ? "" : "s"}
              {onSelect ? (
                <>
                  <br />
                  <em>Click marker for insight details</em>
                </>
              ) : null}
            </Popup>
          </CircleMarker>
        );
      })}
    </MapContainer>
  );
}
