import { apiRequest, clearTokens, readToken, type AuthScope, writeTokens } from "./http";

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface UserProfile {
  public_id?: string;
  username: string;
  email?: string;
  avatar_url?: string;
  role: string;
  is_active?: boolean;
  created_at?: string;
}

export interface EmailCodeResponse {
  status: string;
  message: string;
  expires_in_seconds: number;
  debug_code?: string;
}

function authPrefix(scope: AuthScope): string {
  return scope === "scenic" ? "/api/v1/scenic/auth" : "/api/v1/auth";
}

const authAttemptVersions: Record<AuthScope, number> = { core: 0, scenic: 0 };

async function persistLogin(
  scope: AuthScope,
  attemptVersion: number,
  response: TokenResponse,
): Promise<TokenResponse> {
  if (authAttemptVersions[scope] !== attemptVersion) {
    throw new Error("已有更新的登录请求，本次响应已忽略");
  }
  writeTokens(scope, response.access_token, response.refresh_token);
  return response;
}

export const authApi = {
  async login(scope: AuthScope, username: string, password: string): Promise<TokenResponse> {
    const attemptVersion = ++authAttemptVersions[scope];
    return persistLogin(
      scope,
      attemptVersion,
      await apiRequest<TokenResponse>(`${authPrefix(scope)}/login`, {
        method: "POST",
        timeoutMs: 30_000,
        json: { username, password },
      }),
    );
  },

  async register(scope: AuthScope, username: string, email: string, password: string, code: string): Promise<TokenResponse> {
    const attemptVersion = ++authAttemptVersions[scope];
    return persistLogin(
      scope,
      attemptVersion,
      await apiRequest<TokenResponse>(`${authPrefix(scope)}/register`, {
        method: "POST",
        timeoutMs: 30_000,
        json: { username, email, password, code },
      }),
    );
  },

  requestEmailCode(scope: AuthScope, email: string, purpose: "register" | "login", username = ""): Promise<EmailCodeResponse> {
    return apiRequest<EmailCodeResponse>(`${authPrefix(scope)}/email-code/request`, {
      method: "POST",
        timeoutMs: 30_000,
      json: { email, purpose, username: username || undefined },
    });
  },

  async loginWithEmailCode(scope: AuthScope, email: string, code: string): Promise<TokenResponse> {
    const attemptVersion = ++authAttemptVersions[scope];
    return persistLogin(
      scope,
      attemptVersion,
      await apiRequest<TokenResponse>(`${authPrefix(scope)}/email-code/login`, {
        method: "POST",
        timeoutMs: 30_000,
        json: { email, code },
      }),
    );
  },

  me(scope: AuthScope, signal?: AbortSignal): Promise<UserProfile> {
    return apiRequest<UserProfile>(`${authPrefix(scope)}/me`, { authScope: scope, signal });
  },

  async logout(scope: AuthScope): Promise<void> {
    const logoutVersion = ++authAttemptVersions[scope];
    try {
      const refreshToken = readToken(scope, "refresh");
      await apiRequest<{ status: string }>(`${authPrefix(scope)}/logout`, {
        method: "POST",
        timeoutMs: 30_000,
        headers: refreshToken ? { Authorization: `Bearer ${refreshToken}` } : undefined,
      });
    } finally {
      if (authAttemptVersions[scope] === logoutVersion) clearTokens(scope);
    }
  },
};
