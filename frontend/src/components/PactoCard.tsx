import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

export type PactoStatus = {
  status: "disconnected" | "connected" | "error";
  enabled: boolean;
  last_error: string | null;
  connected_at: string | null;
  last_sync_at: string | null;
  units: {
    id: string;
    pacto_name: string;
    unit_id: string | null;
    movement_text: string | null;
    synced_at: string | null;
  }[];
};

type Unit = { id: string; name: string };

const when = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
    : "nunca";

export default function PactoCard() {
  const [status, setStatus] = useState<PactoStatus | null>(null);
  const [units, setUnits] = useState<Unit[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    Promise.all([api<PactoStatus>("/pacto"), api<Unit[]>("/units")])
      .then(([s, u]) => {
        setStatus(s);
        setUnits(u);
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Toda ação devolve o estado novo da conexão.
  const run = async (request: () => Promise<PactoStatus>) => {
    setBusy(true);
    setError("");
    try {
      setStatus(await request());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao falar com a Pacto");
      load();
    } finally {
      setBusy(false);
    }
  };

  const connect = async () => {
    setBusy(true);
    setError("");
    try {
      const { authorization_url } = await api<{ authorization_url: string }>("/pacto/connect", {
        method: "POST",
        body: JSON.stringify({ redirect_uri: `${window.location.origin}/pacto/callback` }),
      });
      window.location.href = authorization_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao iniciar a conexão com a Pacto");
      setBusy(false);
    }
  };

  if (!status) return error ? <p className="text-ember">{error}</p> : null;

  const connected = status.status === "connected";

  return (
    <div className="space-y-4 border border-white/10 bg-panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-display text-xl font-bold">Pacto</p>
          <p className="mt-1 max-w-2xl text-sm text-sand/55">
            Conecta a Mônica aos relatórios da Pacto. Ela passa a saber sozinha os horários mais cheios e
            mais tranquilos de cada unidade (atualizados todo dia) e consulta a lotação na hora quando o
            cliente pergunta se está cheio agora.
          </p>
        </div>
        <span
          className={`rounded border px-2 py-0.5 text-xs ${
            connected
              ? "border-emerald-400/40 text-emerald-300"
              : status.status === "error"
                ? "border-ember/50 text-ember"
                : "border-white/20 text-sand/60"
          }`}
        >
          {connected ? "Conectada" : status.status === "error" ? "Acesso caiu" : "Não conectada"}
        </span>
      </div>

      {error && <p className="text-sm text-ember">{error}</p>}
      {status.status === "error" && (
        <p className="text-sm text-ember">
          {status.last_error || "A Pacto recusou o acesso."} A IA segue atendendo, só sem esses dados. Reconecte
          pra voltar.
        </p>
      )}

      {!connected ? (
        <button
          onClick={connect}
          disabled={busy}
          className="rounded-md bg-leaf px-4 py-2 font-semibold text-white hover:bg-lime disabled:opacity-50"
        >
          {status.status === "error" ? "Reconectar Pacto" : "Conectar Pacto"}
        </button>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <label className="flex items-center gap-2 text-sand/70">
              <input
                type="checkbox"
                checked={status.enabled}
                disabled={busy}
                onChange={(e) =>
                  run(() =>
                    api<PactoStatus>("/pacto", { method: "PATCH", body: JSON.stringify({ enabled: e.target.checked }) }),
                  )
                }
              />
              A IA usa os dados da Pacto
            </label>
            <span className="text-xs text-sand/45">Última atualização: {when(status.last_sync_at)}</span>
            <button
              onClick={() => run(() => api<PactoStatus>("/pacto/sync", { method: "POST" }))}
              disabled={busy}
              className="rounded-md border border-white/15 px-3 py-1.5 text-sand/70 hover:bg-white/5 disabled:opacity-50"
            >
              {busy ? "Atualizando…" : "Atualizar agora"}
            </button>
            <button
              onClick={() => {
                if (confirm("Desconectar a Pacto? A IA deixa de receber esses dados até conectar de novo.")) {
                  run(() => api<PactoStatus>("/pacto", { method: "DELETE" }));
                }
              }}
              disabled={busy}
              className="rounded-md border border-ember/40 px-3 py-1.5 text-ember hover:bg-ember/10 disabled:opacity-50"
            >
              Desconectar
            </button>
          </div>

          {status.units.length === 0 ? (
            <p className="text-sm text-sand/45">Nenhuma unidade liberada na autorização.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead>
                  <tr className="text-xs uppercase tracking-wider text-muted">
                    <th className="pb-2 pr-4">Unidade na Pacto</th>
                    <th className="pb-2 pr-4">Unidade no catálogo</th>
                    <th className="pb-2">O que a IA sabe do movimento</th>
                  </tr>
                </thead>
                <tbody>
                  {status.units.map((u) => (
                    <tr key={u.id} className="border-t border-white/10 align-top">
                      <td className="py-2 pr-4 text-sand/80">{u.pacto_name}</td>
                      <td className="py-2 pr-4">
                        <select
                          className="w-full rounded-md border border-white/15 bg-ink px-2 py-1.5"
                          value={u.unit_id ?? ""}
                          disabled={busy}
                          onChange={(e) =>
                            run(() =>
                              api<PactoStatus>(`/pacto/units/${u.id}`, {
                                method: "PATCH",
                                body: JSON.stringify({ unit_id: e.target.value || null }),
                              }),
                            )
                          }
                        >
                          <option value="">— não usar —</option>
                          {units.map((unit) => (
                            <option key={unit.id} value={unit.id}>
                              {unit.name}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="py-2 text-sand/60">
                        {u.unit_id
                          ? u.movement_text || "Ainda sem dados — clique em Atualizar agora."
                          : "Vincule a uma unidade do catálogo pra IA usar."}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
