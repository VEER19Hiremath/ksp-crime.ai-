"use client";

import { useEffect } from "react";
import { startKeepAlivePinger, wakeApi } from "@/lib/api";

/**
 * Global Render keep-alive: mounted once in the root layout so any open
 * frontend tab (login, chat, dashboard, …) pings /health every 5 minutes.
 * No GitHub Actions required while someone has the app open.
 */
export default function KeepAlive() {
  useEffect(() => {
    void wakeApi();
    return startKeepAlivePinger();
  }, []);

  return null;
}
