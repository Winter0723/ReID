export type AuthScope = "core" | "scenic";
export type AuthChangeReason = "login" | "refresh" | "logout" | "expired";

export interface AuthSessionChange {
  scope: AuthScope;
  authenticated: boolean;
  reason: AuthChangeReason;
}

export type QueryValue = string | number | boolean | null | undefined;

export interface ApiRequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  query?: Record<string, QueryValue>;
  json?: unknown;
  body?: BodyInit | null;
  authScope?: AuthScope;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

const DEFAULT_API_BASE_URL = import.meta.env.VITE_MYREID_LOCAL_API_BASE_URL || "http://127.0.0.1:2889";
const authSessionListeners = new Set<(change: AuthSessionChange) => void>();
const authSessionVersions: Record<AuthScope, number> = { core: 0, scenic: 0 };
const refreshRequests: Partial<Record<AuthScope, Promise<string>>> = {};

function notifyAuthSession(change: AuthSessionChange): void {
  authSessionListeners.forEach((listener) => listener(change));
}

export function subscribeAuthSession(listener: (change: AuthSessionChange) => void): () => void {
  authSessionListeners.add(listener);
  return () => authSessionListeners.delete(listener);
}

function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean(window.__TAURI_INTERNALS__);
}

function browserUsesProxy(): boolean {
  return !isTauriRuntime() && !import.meta.env.VITE_MYREID_API_BASE_URL;
}

export function getApiBaseUrl(): string {
  return (import.meta.env.VITE_MYREID_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/+$/, "");
}

export function tokenStorageKey(scope: AuthScope, tokenType: "access" | "refresh"): string {
  return `myreid.${scope}.${tokenType}`;
}

export function readToken(scope: AuthScope, tokenType: "access" | "refresh" = "access"): string {
  return localStorage.getItem(tokenStorageKey(scope, tokenType)) || "";
}

export function writeTokens(
  scope: AuthScope,
  accessToken: string,
  refreshToken: string,
  reason: Extract<AuthChangeReason, "login" | "refresh"> = "login",
): void {
  if (reason === "login") authSessionVersions[scope] += 1;
  localStorage.setItem(tokenStorageKey(scope, "access"), accessToken);
  localStorage.setItem(tokenStorageKey(scope, "refresh"), refreshToken);
  notifyAuthSession({ scope, authenticated: true, reason });
}

export function clearTokens(
  scope: AuthScope,
  reason: Extract<AuthChangeReason, "logout" | "expired"> = "logout",
): void {
  authSessionVersions[scope] += 1;
  delete refreshRequests[scope];
  localStorage.removeItem(tokenStorageKey(scope, "access"));
  localStorage.removeItem(tokenStorageKey(scope, "refresh"));
  notifyAuthSession({ scope, authenticated: false, reason });
}

function withQuery(path: string, query?: Record<string, QueryValue>): string {
  if (!query) return path;
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  });
  const queryString = params.toString();
  if (!queryString) return path;
  return `${path}${path.includes("?") ? "&" : "?"}${queryString}`;
}

function resolveRequestUrl(path: string, query?: Record<string, QueryValue>): string {
  const requested = withQuery(path, query);
  if (/^https?:\/\//i.test(requested)) return requested;
  if (browserUsesProxy()) return requested;
  return `${getApiBaseUrl()}${requested.startsWith("/") ? requested : `/${requested}`}`;
}

async function transportFetch(input: string, init: RequestInit): Promise<Response> {
  if (isTauriRuntime()) {
    const module = await import("@tauri-apps/plugin-http");
    return module.fetch(input, init);
  }
  return window.fetch(input, init);
}

async function responseDetail(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }
  try {
    return await response.text();
  } catch {
    return null;
  }
}

function responseMessage(response: Response, detail: unknown): string {
  const value = typeof detail === "object" && detail && "detail" in detail
    ? (detail as { detail: unknown }).detail
    : detail;
  if (typeof value === "string" && value.trim()) return value;
  if (Array.isArray(value)) {
    const messages = value
      .map((item) => (typeof item === "object" && item && "msg" in item ? String(item.msg) : ""))
      .filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  return `${response.status} ${response.statusText}`;
}

async function refreshAccessToken(scope: AuthScope): Promise<string> {
  if (refreshRequests[scope]) return refreshRequests[scope];

  const pending = (async () => {
    const refreshToken = readToken(scope, "refresh");
    const sessionVersion = authSessionVersions[scope];
    if (!refreshToken) {
      throw new ApiError(401, "登录已过期，请重新登录", null);
    }

    const prefix = scope === "scenic" ? "/api/v1/scenic/auth" : "/api/v1/auth";
    const response = await transportFetch(resolveRequestUrl(`${prefix}/refresh`), {
      method: "POST",
      headers: { Authorization: `Bearer ${refreshToken}` },
      body: null,
    });

    if (
      authSessionVersions[scope] !== sessionVersion
      || readToken(scope, "refresh") !== refreshToken
    ) {
      throw new ApiError(401, "登录状态已变更，已忽略过期的刷新响应", null);
    }

    if (!response.ok) {
      const detail = await responseDetail(response);
      if (response.status === 400 || response.status === 401 || response.status === 403) {
        clearTokens(scope, "expired");
      }
      throw new ApiError(response.status, responseMessage(response, detail), detail);
    }

    const tokens = (await response.json()) as { access_token?: string; refresh_token?: string };
    if (
      authSessionVersions[scope] !== sessionVersion
      || readToken(scope, "refresh") !== refreshToken
    ) {
      throw new ApiError(401, "登录状态已变更，已忽略过期的刷新响应", null);
    }
    if (!tokens.access_token || !tokens.refresh_token) {
      clearTokens(scope, "expired");
      throw new ApiError(401, "刷新令牌响应无效，请重新登录", tokens);
    }
    writeTokens(scope, tokens.access_token, tokens.refresh_token, "refresh");
    return tokens.access_token;
  })();

  refreshRequests[scope] = pending;
  try {
    return await pending;
  } finally {
    if (refreshRequests[scope] === pending) delete refreshRequests[scope];
  }
}

async function sendApiRequest(path: string, options: ApiRequestOptions, allowRefresh: boolean): Promise<Response> {
  const headers: Record<string, string> = { ...(options.headers || {}) };
  let body = options.body ?? null;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }
  const accessToken = options.authScope ? readToken(options.authScope) : "";
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  const response = await transportFetch(resolveRequestUrl(path, options.query), {
    method: options.method || "GET",
    headers,
    body,
    signal: options.signal,
  });

  if (
    response.status === 401
    && options.authScope
    && allowRefresh
    && Boolean(accessToken)
    && Boolean(readToken(options.authScope, "refresh"))
  ) {
    const token = await refreshAccessToken(options.authScope);
    headers.Authorization = `Bearer ${token}`;
    const retriedResponse = await transportFetch(resolveRequestUrl(path, options.query), {
      method: options.method || "GET",
      headers,
      body,
      signal: options.signal,
    });
    if (retriedResponse.status === 401) {
      clearTokens(options.authScope, "expired");
    }
    return retriedResponse;
  }
  return response;
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (options.signal?.aborted) cancel();
  options.signal?.addEventListener("abort", cancel, { once: true });
  let timedOut = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
      reject(new ApiError(408, "请求超时，请检查服务连接后重试", null));
    }, options.timeoutMs ?? 120_000);
  });
  try {
    return await Promise.race([timeout, (async () => {
      const response = await sendApiRequest(path, { ...options, signal: controller.signal }, true);
      if (!response.ok) {
        const detail = await responseDetail(response);
        throw new ApiError(response.status, responseMessage(response, detail), detail);
      }
      if (response.status === 204) return undefined as T;
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) return (await response.json()) as T;
      if (contentType.includes("text/html")) {
        throw new ApiError(502, "接口返回了网页，请检查生产环境 API 地址和反向代理配置", null);
      }
      return (await response.text()) as T;
    })()]);
  } catch (error) {
    if (timedOut) throw new ApiError(408, "请求超时，请检查服务连接后重试", null);
    if (error instanceof TypeError) throw new ApiError(0, "无法连接服务，请检查网络和 API 地址", null);
    throw error;
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", cancel);
  }
}

export async function apiBlob(path: string, options: ApiRequestOptions = {}): Promise<Blob> {
  const response = await sendApiRequest(path, options, true);
  if (!response.ok) {
    const detail = await responseDetail(response);
    throw new ApiError(response.status, responseMessage(response, detail), detail);
  }
  return response.blob();
}

export function normalizeAssetUrl(value?: string | null): string {
  if (!value) return "";
  if (value.startsWith("blob:") || value.startsWith("data:")) return value;
  if (/^https?:\/\//i.test(value)) return encodeAssetPath(value);

  let assetPath = value.trim();
  if (assetPath.startsWith("./gallery/")) {
    assetPath = assetPath.slice(1);
  } else if (assetPath.startsWith("gallery/")) {
    assetPath = `/${assetPath}`;
  } else if (assetPath.startsWith("./object_storage/")) {
    assetPath = `/objects/${assetPath.slice("./object_storage/".length)}`;
  } else if (assetPath.startsWith("object_storage/")) {
    assetPath = `/objects/${assetPath.slice("object_storage/".length)}`;
  } else if (assetPath.startsWith("projects/")) {
    assetPath = `/objects/${assetPath}`;
  } else if (!assetPath.startsWith("/")) {
    assetPath = `/${assetPath}`;
  }

  if (browserUsesProxy()) return encodeAssetPath(assetPath);
  return encodeAssetPath(`${getApiBaseUrl()}${assetPath}`);
}

function encodeAssetPath(value: string): string {
  try {
    const isAbsolute = /^https?:\/\//i.test(value);
    const parsed = new URL(value, isAbsolute ? undefined : "http://myreid.local");
    parsed.pathname = parsed.pathname
      .split("/")
      .map((part) => (part ? encodeURIComponent(decodeURIComponent(part)) : part))
      .join("/");
    if (isAbsolute) return parsed.toString();
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return encodeURI(value);
  }
}

export async function saveBlob(blob: Blob, filename: string): Promise<boolean> {
  if (!isTauriRuntime()) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.hidden = true;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    return true;
  }

  const [{ save }, { writeFile }] = await Promise.all([
    import("@tauri-apps/plugin-dialog"),
    import("@tauri-apps/plugin-fs"),
  ]);
  const selectedPath = await save({ defaultPath: filename });
  if (!selectedPath) return false;
  await writeFile(selectedPath, new Uint8Array(await blob.arrayBuffer()));
  return true;
}

export async function downloadAsset(
  source: string,
  filename: string,
  authScope?: AuthScope,
): Promise<boolean> {
  if (!source) return false;
  const blob = await apiBlob(normalizeAssetUrl(source), { authScope });
  return saveBlob(blob, filename);
}
