const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export type Tokens = {
  access_token: string;
  refresh_token: string;
};

function getStored(key: string) {
  return localStorage.getItem(key);
}

export function setTokens(tokens: Tokens) {
  localStorage.setItem("access_token", tokens.access_token);
  localStorage.setItem("refresh_token", tokens.refresh_token);
}

export function clearTokens() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("company_id");
}

export function setCompanyId(id: string) {
  localStorage.setItem("company_id", id);
}

export function getCompanyId() {
  return getStored("company_id");
}

export async function api<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers || {});
  headers.set("Content-Type", "application/json");
  const token = getStored("access_token");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const companyId = getCompanyId();
  if (companyId) headers.set("X-Company-Id", companyId);

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });
  if (!res.ok) {
    let detail = "Erro na requisição";
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// Pra páginas fora do login (ex: PublicMetricsPage) — autentica com uma
// chave de API (Authorization: Bearer) em vez de sessão de usuário, sem
// nenhuma das permissões de admin do painel normal.
export async function apiExternal<T>(path: string, key: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${key}` },
  });
  if (!res.ok) {
    let detail = "Erro na requisição";
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

export async function apiUpload<T>(path: string, file: File): Promise<T> {
  const headers = new Headers();
  const token = getStored("access_token");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const companyId = getCompanyId();
  if (companyId) headers.set("X-Company-Id", companyId);
  // Sem Content-Type manual: o browser define multipart/form-data com o
  // boundary certo sozinho ao ver um body FormData.

  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${API_URL}${path}`, { method: "POST", headers, body: formData });
  if (!res.ok) {
    let detail = "Erro no envio do arquivo";
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

export { API_URL };
