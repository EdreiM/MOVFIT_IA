import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

type PanelUser = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  status: string;
};

const roleLabel: Record<string, string> = {
  super_admin: "Super administrador",
  admin: "Administrador (painel todo)",
  vendas: "Supervisão de vendas (só o ranking)",
  atendente: "Atendente (sem acesso ao painel)",
};

export default function UsersPage() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<PanelUser[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("vendas");

  const [resetFor, setResetFor] = useState<string | null>(null);
  const [resetPassword, setResetPassword] = useState("");

  const load = useCallback(async () => {
    try {
      setUsers(await api<PanelUser[]>("/users"));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (action: () => Promise<unknown>, success: string) => {
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(success);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (password.length < 8) {
      setError("A senha precisa ter pelo menos 8 caracteres.");
      return;
    }
    setSaving(true);
    await run(
      () =>
        api("/users", {
          method: "POST",
          body: JSON.stringify({ full_name: fullName.trim(), email: email.trim(), password, role }),
        }),
      `Conta criada para ${email.trim()}. Passe o e-mail e a senha para a pessoa.`
    );
    setSaving(false);
    setFullName("");
    setEmail("");
    setPassword("");
  };

  const setStatus = (u: PanelUser, status: "active" | "inactive") =>
    run(
      () => api(`/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
      status === "active" ? `${u.full_name} reativada(o).` : `${u.full_name} desativada(o): não consegue mais entrar.`
    );

  const submitReset = async (u: PanelUser) => {
    if (resetPassword.length < 8) {
      setError("A nova senha precisa ter pelo menos 8 caracteres.");
      return;
    }
    await run(
      () => api(`/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ password: resetPassword }) }),
      `Senha de ${u.full_name} redefinida.`
    );
    setResetFor(null);
    setResetPassword("");
  };

  const remove = (u: PanelUser) => {
    if (!window.confirm(`Excluir a conta de ${u.full_name} (${u.email})? Isso não pode ser desfeito.`)) return;
    run(() => api(`/users/${u.id}`, { method: "DELETE" }), `Conta de ${u.full_name} excluída.`);
  };

  return (
    <div className="space-y-10">
      <div>
        <h2 className="font-display text-3xl font-bold">Usuários</h2>
        <p className="mt-1 text-sand/55">
          Quem acessa este painel. Uma conta de <strong>supervisão de vendas</strong> enxerga somente o Ranking
          de vendas — nenhuma outra guia.
        </p>
      </div>

      {error && <p className="text-ember">{error}</p>}
      {notice && <p className="text-lime">{notice}</p>}

      <form onSubmit={create} className="max-w-xl space-y-4 border border-white/10 bg-panel p-5">
        <h3 className="font-medium">Nova conta</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block space-y-1">
            <span className="text-sm text-sand/60">Nome</span>
            <input
              className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              required
            />
          </label>
          <label className="block space-y-1">
            <span className="text-sm text-sand/60">E-mail (é o login)</span>
            <input
              type="email"
              className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label className="block space-y-1">
            <span className="text-sm text-sand/60">Senha inicial</span>
            <input
              type="password"
              autoComplete="new-password"
              className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Mínimo 8 caracteres"
              required
            />
          </label>
          <label className="block space-y-1">
            <span className="text-sm text-sand/60">Acesso</span>
            <select
              className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
              value={role}
              onChange={(e) => setRole(e.target.value)}
            >
              <option value="vendas">{roleLabel.vendas}</option>
              <option value="admin">{roleLabel.admin}</option>
            </select>
          </label>
        </div>
        <button
          type="submit"
          disabled={saving}
          className="rounded-md bg-leaf px-4 py-2 font-medium text-white disabled:opacity-60"
        >
          {saving ? "Criando…" : "Criar conta"}
        </button>
      </form>

      <section className="space-y-3">
        <h3 className="font-display text-xl font-semibold">Contas</h3>
        <ul className="divide-y divide-white/10 border border-white/10">
          {users.map((u) => {
            const isMe = u.id === me?.id;
            const inactive = u.status !== "active";
            return (
              <li key={u.id} className="space-y-3 px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="font-medium">
                      {u.full_name}
                      {isMe && <span className="ml-2 text-xs text-sand/45">(você)</span>}
                      {inactive && (
                        <span className="ml-2 rounded-md border border-white/20 px-2 py-0.5 text-xs text-sand/60">
                          desativada
                        </span>
                      )}
                    </p>
                    <p className="text-sm text-sand/55">
                      {u.email} · {roleLabel[u.role] ?? u.role}
                    </p>
                  </div>
                  {!isMe && u.role !== "super_admin" && (
                    <div className="flex flex-wrap gap-2 text-sm">
                      <button
                        type="button"
                        onClick={() => {
                          setResetFor(resetFor === u.id ? null : u.id);
                          setResetPassword("");
                        }}
                        className="rounded-md border border-white/15 px-3 py-1.5 text-sand/80 hover:bg-white/5"
                      >
                        Redefinir senha
                      </button>
                      <button
                        type="button"
                        onClick={() => setStatus(u, inactive ? "active" : "inactive")}
                        className="rounded-md border border-white/15 px-3 py-1.5 text-sand/80 hover:bg-white/5"
                      >
                        {inactive ? "Reativar" : "Desativar"}
                      </button>
                      <button
                        type="button"
                        onClick={() => remove(u)}
                        className="rounded-md border border-ember/40 px-3 py-1.5 text-ember hover:bg-ember/10"
                      >
                        Excluir
                      </button>
                    </div>
                  )}
                </div>
                {resetFor === u.id && (
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      type="password"
                      autoComplete="new-password"
                      placeholder="Nova senha (mínimo 8)"
                      value={resetPassword}
                      onChange={(e) => setResetPassword(e.target.value)}
                      className="rounded-md border border-white/15 bg-ink px-3 py-2 text-sm"
                    />
                    <button
                      type="button"
                      onClick={() => submitReset(u)}
                      className="rounded-md bg-leaf px-3 py-2 text-sm font-medium text-white"
                    >
                      Salvar senha
                    </button>
                  </div>
                )}
              </li>
            );
          })}
          {users.length === 0 && <li className="px-4 py-8 text-center text-sand/45">Nenhuma conta.</li>}
        </ul>
      </section>
    </div>
  );
}
