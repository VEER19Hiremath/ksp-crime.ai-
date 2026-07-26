const TOKEN_KEY = "crimeai_token";
const ROLE_KEY = "crimeai_role";
const NAME_KEY = "crimeai_full_name";
const USER_KEY = "crimeai_username";

export function saveSession(token: string, role: string, fullName: string, username?: string) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(ROLE_KEY, role);
  localStorage.setItem(NAME_KEY, fullName);
  if (username) localStorage.setItem(USER_KEY, username);
}

export function getToken(): string | null {
  return typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY);
}

export function getRole(): string | null {
  return typeof window === "undefined" ? null : localStorage.getItem(ROLE_KEY);
}

export function getFullName(): string | null {
  return typeof window === "undefined" ? null : localStorage.getItem(NAME_KEY);
}

export function getUsername(): string | null {
  return typeof window === "undefined" ? null : localStorage.getItem(USER_KEY);
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
  localStorage.removeItem(NAME_KEY);
  localStorage.removeItem(USER_KEY);
}

/** Roles allowed to export investigation PDFs — mirrors backend/routers/reports.py's
 * require_role(...) so the button can be hidden before the request ever 403s. */
export const PDF_EXPORT_ROLES = ["SHO", "DSP", "Analyst", "Administrator"];
