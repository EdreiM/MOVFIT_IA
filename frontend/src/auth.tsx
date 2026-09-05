import React, { createContext, useContext, useEffect, useState } from "react";
import { api, clearTokens, setCompanyId, setTokens, Tokens } from "./api";

export type User = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  status: string;
};

export type Company = {
  id: string;
  name: string;
  slug: string;
  status: string;
  plan: string;
};

type AuthState = {
  user: User | null;
  companies: Company[];
  companyId: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  selectCompany: (id: string) => void;
  refreshProfile: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyIdState] = useState<string | null>(
    localStorage.getItem("company_id")
  );
  const [loading, setLoading] = useState(true);

  const refreshProfile = async () => {
    const me = await api<User>("/auth/me");
    const comps = await api<Company[]>("/auth/me/companies");
    setUser(me);
    setCompanies(comps);
    if (!localStorage.getItem("company_id") && comps[0]) {
      setCompanyId(comps[0].id);
      setCompanyIdState(comps[0].id);
    }
  };

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) {
      setLoading(false);
      return;
    }
    refreshProfile()
      .catch(() => {
        clearTokens();
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = async (email: string, password: string) => {
    const tokens = await api<Tokens>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setTokens(tokens);
    await refreshProfile();
  };

  const logout = () => {
    clearTokens();
    setUser(null);
    setCompanies([]);
    setCompanyIdState(null);
  };

  const selectCompany = (id: string) => {
    setCompanyId(id);
    setCompanyIdState(id);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        companies,
        companyId,
        loading,
        login,
        logout,
        selectCompany,
        refreshProfile,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth fora do AuthProvider");
  return ctx;
}
