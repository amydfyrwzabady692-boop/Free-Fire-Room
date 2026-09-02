const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080/api";

const ACCESS_KEY = "ff_token";
const REFRESH_KEY = "ff_refresh";

export type Tokens = { access_token: string; refresh_token?: string | null };

export function saveTokens(data: Tokens) {
  if (typeof window === "undefined") return;
  localStorage.setItem(ACCESS_KEY, data.access_token);
  if (data.refresh_token) localStorage.setItem(REFRESH_KEY, data.refresh_token);
}

export function clearTokens() {
  if (typeof window === "undefined") return;
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

function token(key: string) {
  return typeof window === "undefined" ? null : localStorage.getItem(key);
}

/** One in-flight refresh shared by every request that hits a 401 at once. */
let refreshing: Promise<boolean> | null = null;

async function refresh(): Promise<boolean> {
  const stored = token(REFRESH_KEY);
  if (!stored) return false;
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch(`${API}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: stored }),
        });
        if (!res.ok) return false;
        saveTokens(await res.json());
        return true;
      } catch {
        return false;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

function toLogin() {
  clearTokens();
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}

async function call(path: string, options: RequestInit) {
  const access = token(ACCESS_KEY);
  return fetch(`${API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(access ? { Authorization: `Bearer ${access}` } : {}),
      ...(options.headers || {}),
    },
  });
}

export async function api(path: string, options: RequestInit = {}) {
  let res = await call(path, options);

  // the access token only lives 20 minutes; a silent refresh keeps the panel
  // usable instead of throwing "server error" at the user
  if (res.status === 401 && !path.startsWith("/auth/")) {
    if (await refresh()) {
      res = await call(path, options);
    }
    if (res.status === 401) {
      toLogin();
      throw new Error("نشست شما منقضی شد. دوباره وارد شوید.");
    }
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 403) throw new Error(data.message || "دسترسی شما به این بخش مجاز نیست.");
    if (res.status === 429) throw new Error(data.message || "درخواست بیش از حد. کمی بعد تلاش کنید.");
    throw new Error(data.message || data.detail?.message || data.detail || "خطای سرور");
  }
  return data;
}

export async function logout() {
  const stored = token(REFRESH_KEY);
  if (stored) {
    try {
      await fetch(`${API}/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: stored }),
      });
    } catch {
      /* logging out locally is what matters */
    }
  }
  clearTokens();
}
