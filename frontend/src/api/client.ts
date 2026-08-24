import type { ApiErrorPayload } from "./types";

const API_PREFIX = "/api/v1";
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const CSRF_REJECTION_CODES = new Set(["csrf_failed", "csrf_invalid"]);
const SESSION_INVALID_CODES = new Set(["authentication_required", "invalid_session"]);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;

  constructor(message: string, status: number, code = "request_failed", requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  skipCsrf?: boolean;
};

let csrfToken: string | null = null;
let csrfPromise: Promise<string> | null = null;

function makeUrl(path: string, query?: RequestOptions["query"]): string {
  const url = `${API_PREFIX}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  });
  const serialized = params.toString();
  return serialized ? `${url}?${serialized}` : url;
}

async function parseError(response: Response): Promise<ApiError> {
  let payload: ApiErrorPayload | null = null;
  try {
    payload = (await response.json()) as ApiErrorPayload;
  } catch {
    // Proxies can return an empty or non-JSON response. Keep it user-safe.
  }
  const detail = payload?.error;
  const fallback = response.status >= 500
    ? "서버에서 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."
    : "요청을 완료할 수 없습니다.";
  return new ApiError(detail?.message ?? fallback, response.status, detail?.code, detail?.request_id);
}

async function loadCsrfToken(): Promise<string> {
  const response = await fetch(`${API_PREFIX}/auth/csrf`, {
    cache: "no-store",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw await parseError(response);
  const payload = (await response.json()) as { csrf_token?: string };
  if (!payload.csrf_token) {
    throw new ApiError("보안 토큰을 발급받지 못했습니다.", 500, "csrf_token_missing");
  }
  csrfToken = payload.csrf_token;
  return payload.csrf_token;
}

export async function ensureCsrfToken(force = false): Promise<string> {
  if (force) csrfToken = null;
  if (csrfToken) return csrfToken;
  if (!csrfPromise) {
    csrfPromise = loadCsrfToken().finally(() => {
      csrfPromise = null;
    });
  }
  return csrfPromise;
}

export function resetCsrfToken(): void {
  csrfToken = null;
  csrfPromise = null;
}

export function isSessionInvalidError(error: unknown): error is ApiError {
  return error instanceof ApiError
    && error.status === 401
    && SESSION_INVALID_CODES.has(error.code);
}

async function executeApiRequest<T>(
  path: string,
  options: RequestOptions,
  canRetryCsrf: boolean,
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const usesCsrf = MUTATING_METHODS.has(method) && !options.skipCsrf;
  let requestCsrfToken: string | null = null;
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");

  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (usesCsrf) {
    requestCsrfToken = await ensureCsrfToken();
    headers.set("X-CSRF-Token", requestCsrfToken);
  }

  const response = await fetch(makeUrl(path, options.query), {
    ...options,
    method,
    cache: "no-store",
    credentials: "same-origin",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    const error = await parseError(response);
    if (isSessionInvalidError(error)) {
      window.dispatchEvent(new CustomEvent("alpha:unauthorized"));
    }
    if (usesCsrf && error.status === 403 && CSRF_REJECTION_CODES.has(error.code)) {
      if (canRetryCsrf) {
        if (csrfToken === requestCsrfToken) await ensureCsrfToken(true);
        else await ensureCsrfToken();
        return executeApiRequest<T>(path, options, false);
      }
      resetCsrfToken();
    }
    throw error;
  }

  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return undefined as T;
  return (await response.json()) as T;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return executeApiRequest<T>(path, options, true);
}

export const http = {
  get: <T>(path: string, options?: RequestOptions) => apiRequest<T>(path, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "POST", body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "PUT", body }),
  delete: <T>(path: string, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "DELETE" }),
};
