import { FormEvent, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../auth";

export default function LoginPage() {
  const { user, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha no login");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative min-h-screen overflow-hidden bg-mesh">
      <div className="absolute inset-0 bg-[url('data:image/svg+xml,%3Csvg width=%2760%27 height=%2760%27 viewBox=%270 0 60 60%27 xmlns=%27http://www.w3.org/2000/svg%27%3E%3Cg fill=%27none%27 fill-rule=%27evenodd%27%3E%3Cg fill=%27%23E51C24%27 fill-opacity=%270.05%27%3E%3Cpath d=%27M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z%27/%3E%3C/g%3E%3C/g%3E%3C/svg%3E')] opacity-70" />
      <div className="absolute -right-20 top-10 h-[28rem] w-[28rem] rounded-full bg-leaf/30 blur-3xl animate-pulse-soft" />

      <div className="relative z-10 mx-auto flex min-h-screen max-w-6xl flex-col justify-center px-6 py-16 lg:flex-row lg:items-end lg:justify-between lg:gap-16">
        <div className="mb-12 max-w-xl animate-rise lg:mb-0">
          <p className="font-display text-6xl font-extrabold leading-none tracking-tight text-lime sm:text-7xl">
            MOV FIT IA
          </p>
          <h1 className="mt-6 font-display text-2xl font-semibold text-sand/90 sm:text-3xl">
            IA de atendimento para academias que crescem.
          </h1>
          <p className="mt-4 max-w-md text-muted">
            Controle conversas, canais e o cérebro da IA em um só lugar — começando pela Mov Fit.
          </p>
        </div>

        <form
          onSubmit={onSubmit}
          className="w-full max-w-md animate-rise space-y-4 rounded-xl border border-white/10 bg-panel p-8 shadow-brand"
          style={{ animationDelay: "0.12s" }}
        >
          <p className="text-sm uppercase tracking-[0.18em] text-muted">Acesso</p>
          <label className="block space-y-1.5">
            <span className="text-sm text-sand/80">E-mail</span>
            <input
              className="w-full rounded-lg border border-white/10 bg-ink px-3 py-2.5 outline-none ring-leaf focus:ring-2"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm text-sand/80">Senha</span>
            <input
              className="w-full rounded-lg border border-white/10 bg-ink px-3 py-2.5 outline-none ring-leaf focus:ring-2"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          {error && <p className="text-sm text-ember">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-leaf px-4 py-3 font-semibold text-white transition hover:bg-lime disabled:opacity-60"
          >
            {busy ? "Entrando…" : "Entrar"}
          </button>
        </form>
      </div>
    </div>
  );
}
