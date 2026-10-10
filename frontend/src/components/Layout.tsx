import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

const navGroups: { label: string; links: { to: string; label: string }[] }[] = [
  {
    label: "Atendimento",
    links: [
      { to: "/", label: "Métricas" },
      { to: "/conversations", label: "Conversas" },
      { to: "/leads", label: "Clientes" },
    ],
  },
  {
    label: "Operação",
    links: [
      { to: "/integrations", label: "Integrações" },
      { to: "/alerts", label: "Alertas" },
      { to: "/webhook-logs", label: "Logs de Webhook" },
      { to: "/test-chat", label: "Chat de teste" },
    ],
  },
  {
    label: "Configuração",
    links: [
      { to: "/ai", label: "Config. IA" },
      { to: "/catalog", label: "Unidades & Planos" },
      { to: "/promotions", label: "Promoções" },
      { to: "/tools", label: "Ferramentas" },
      { to: "/api-keys", label: "API externa" },
    ],
  },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, companies, companyId, selectCompany, logout } = useAuth();
  const [activeAlerts, setActiveAlerts] = useState(0);

  // Bolinha no menu "Alertas" — o painel avisa sem precisar abrir a tela.
  useEffect(() => {
    if (!companyId) return;
    let cancelled = false;
    const poll = () =>
      api<{ count: number }>("/admin/events/active-count")
        .then((r) => !cancelled && setActiveAlerts(r.count))
        .catch(() => undefined);
    poll();
    const timer = setInterval(poll, 60_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [companyId]);

  return (
    <div className="min-h-screen bg-mesh text-sand">
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -left-24 top-24 h-72 w-72 rounded-full bg-leaf/20 blur-3xl animate-pulse-soft" />
        <div className="absolute right-0 top-0 h-96 w-96 rounded-full bg-ember/10 blur-3xl" />
      </div>

      <header className="relative z-10 border-b border-white/10 backdrop-blur-md">
        <div className="mx-auto max-w-6xl px-6">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 pt-4">
            <div className="animate-rise">
              <p className="font-display text-2xl font-extrabold tracking-tight text-lime">
                MOV FIT IA
              </p>
              <p className="text-xs uppercase tracking-[0.2em] text-sand/50">
                Painel de Atendimento
              </p>
            </div>

            <div className="flex items-center gap-3 text-sm">
              {companies.length > 1 && (
                <select
                  className="rounded-md border border-white/15 bg-ink/60 px-2 py-1.5"
                  value={companyId || ""}
                  onChange={(e) => selectCompany(e.target.value)}
                >
                  {companies.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              )}
              <NavLink
                to="/account"
                className={({ isActive }) =>
                  `hidden text-sand/60 hover:text-sand sm:inline ${isActive ? "text-lime" : ""}`
                }
              >
                {user?.full_name}
              </NavLink>
              <button
                onClick={logout}
                className="rounded-md border border-white/15 px-3 py-1.5 text-sand/80 hover:border-ember/50 hover:text-ember"
              >
                Sair
              </button>
            </div>
          </div>

          {/* Uma linha só: abaixo de xl não cabe tudo, então rola na horizontal em vez de quebrar */}
          <nav className="no-scrollbar -mx-2 mt-2 flex items-center overflow-x-auto px-2 py-3 xl:overflow-visible">
            {navGroups.map((group, i) => (
              <div
                key={group.label}
                role="group"
                aria-label={group.label}
                title={group.label}
                className={`flex shrink-0 items-center gap-0.5 ${
                  i > 0 ? "ml-2 border-l border-white/15 pl-2" : ""
                }`}
              >
                {group.links.map((l) => (
                  <NavLink
                    key={l.to}
                    to={l.to}
                    end={l.to === "/"}
                    className={({ isActive }) =>
                      `whitespace-nowrap rounded-md px-2.5 py-1.5 text-[13px] transition ${
                        isActive
                          ? "bg-leaf text-white shadow-brand"
                          : "text-muted hover:bg-white/5 hover:text-sand"
                      }`
                    }
                  >
                    {l.label}
                    {l.to === "/alerts" && activeAlerts > 0 && (
                      <span className="ml-1.5 rounded-full bg-ember px-1.5 py-0.5 text-[10px] font-bold text-white">
                        {activeAlerts}
                      </span>
                    )}
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>
        </div>
      </header>

      <main className="relative z-10 mx-auto max-w-6xl px-6 py-8 animate-rise">
        {children}
      </main>
    </div>
  );
}
