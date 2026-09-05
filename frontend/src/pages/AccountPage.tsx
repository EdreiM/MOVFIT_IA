import { FormEvent, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

export default function AccountPage() {
  const { user } = useAuth();
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [saving, setSaving] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setSuccess(false);
    if (newPassword.length < 8) {
      setError("A senha precisa ter pelo menos 8 caracteres.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("As senhas não coincidem.");
      return;
    }
    if (!user) return;
    setSaving(true);
    try {
      await api(`/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ password: newPassword }),
      });
      setSuccess(true);
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao trocar senha");
    } finally {
      setSaving(false);
    }
  };

  if (!user) return null;

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl font-bold">Minha conta</h2>
        <p className="mt-1 text-sand/55">{user.full_name} · {user.email}</p>
      </div>

      <form onSubmit={onSubmit} className="max-w-md space-y-4 border border-white/10 bg-panel p-5">
        <h3 className="font-medium">Trocar senha</h3>
        <label className="block space-y-1">
          <span className="text-sm text-sand/60">Nova senha</span>
          <input
            type="password"
            className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="Mínimo 8 caracteres"
            required
          />
        </label>
        <label className="block space-y-1">
          <span className="text-sm text-sand/60">Confirmar nova senha</span>
          <input
            type="password"
            className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
          />
        </label>

        {error && <p className="text-ember">{error}</p>}
        {success && <p className="text-lime">Senha trocada com sucesso!</p>}

        <button
          type="submit"
          disabled={saving}
          className="rounded-md bg-leaf px-5 py-2.5 font-semibold text-white hover:bg-lime disabled:opacity-50"
        >
          {saving ? "Salvando…" : "Salvar nova senha"}
        </button>
      </form>
    </div>
  );
}
