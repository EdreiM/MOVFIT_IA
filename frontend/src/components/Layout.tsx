import { NavLink } from "react-router-dom";
import { useAuth } from "../auth";

const links = [
  { to: "/", label: "Métricas" },
  { to: "/conversations", label: "Conversas" },
  { to: "/leads", label: "Clientes" },
  { to: "/numbers", label: "Números" },
  { to: "/integrations", label: "Integrações" },
  { to: "/ai", label: "Config. IA" },
  { to: "/catalog", label: "Unidades & Planos" },
  { to: "/tools", label: "Ferramentas" },
  { to: "/test-chat", label: "Chat de teste" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, companies, companyId, selectCompany, logout } = useAuth();

  return (
    <div className="min-h-screen bg-mesh text-sand">
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -left-24 top-24 h-72 w-72 rounded-full bg-leaf/20 blur-3xl animate-pulse-soft" />
        <div className="absolute right-0 top-0 h-96 w-96 rounded-full bg-ember/10 blur-3xl" />
      </div>

      <header className="relative z-10 border-b border-white/10 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-4">
          <div className="animate-rise">
            <p className="font-display text-2xl font-extrabold tracking-tight text-lime">
              MOV FIT IA
            </p>
            <p className="text-xs uppercase tracking-[0.2em] text-sand/50">
              Painel de Atendimento
            </p>
          </div>

          <nav className="flex flex-wrap gap-1">
            {links.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                end={l.to === "/"}
                className={({ isActive }) =>
                  `rounded-md px-3 py-2 text-sm transition ${
                    isActive
                      ? "bg-leaf text-white shadow-brand"
                      : "text-muted hover:bg-white/5 hover:text-sand"
                  }`
                }
              >
                {l.label}
              </NavLink>
            ))}
          </nav>

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
      </header>

      <main className="relative z-10 mx-auto max-w-6xl px-6 py-8 animate-rise">
        {children}
      </main>
    </div>
  );
}
