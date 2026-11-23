import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

/** Error with the server's message (FastAPI "detail") and the HTTP status. */
export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// Changes need this header (CSRF protection); sending it on every request keeps things simple.
const HEADERS = { "X-Programarr": "1", Accept: "application/json" };

export const api = createClient<paths>({ headers: HEADERS });

let unauthorized: (() => void) | null = null;

/** Called when the server answers 401: the session ended, show the sign-in page. */
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorized = handler;
}

api.use({
  onResponse({ response }) {
    if (response.status === 401 && unauthorized && !new URL(response.url, location.href).pathname.startsWith("/api/auth/")) {
      unauthorized();
    }
    return undefined;
  },
});

function detailOf(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((entry) => (entry && typeof entry === "object" && "msg" in entry ? String(entry.msg) : String(entry))).join(", ");
    }
  }
  if (status === 502 || status === 503 || status === 504) return "Programarr is not reachable";
  return `Request failed (${status})`;
}

type FetchResult<T> = { data?: T; error?: unknown; response: Response };

/** Data of a successful request; throws ApiError with the server's message otherwise. */
export async function unwrap<T>(request: Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await request;
  } catch {
    throw new ApiError("Programarr is not reachable", 0);
  }
  if (!result.response.ok || result.error !== undefined) {
    throw new ApiError(detailOf(result.error, result.response.status), result.response.status);
  }
  return result.data as T;
}

export async function uploadImage(file: File, kind: string, season: number | null): Promise<Schemas["ImageView"]> {
  const form = new FormData();
  form.append("file", file);
  const query = new URLSearchParams({ kind });
  if (season !== null) query.set("season", String(season));
  let response: Response;
  try {
    response = await fetch(`/api/upload?${query}`, { method: "POST", body: form, headers: HEADERS });
  } catch {
    throw new ApiError("Programarr is not reachable", 0);
  }
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 401 && unauthorized) unauthorized();
  if (!response.ok) throw new ApiError(detailOf(body, response.status), response.status);
  return body as Schemas["ImageView"];
}

export function errorText(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}
