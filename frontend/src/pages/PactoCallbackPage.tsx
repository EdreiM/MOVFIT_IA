import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";

// Pra onde a Pacto devolve o usuário depois da tela de autorização: conclui a
// conexão no backend e volta pra Integrações.
export default function PactoCallbackPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  // O código de autorização só vale uma vez — sem isso, o efeito duplo do
  // React em desenvolvimento tentaria usá-lo duas vezes.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const code = params.get("code");
    const state = params.get("state");
    if (params.get("error") || !code || !state) {
      setError(
        params.get("error_description") || "A autorização na Pacto foi cancelada ou não foi concluída.",
      );
      return;
    }
    api("/pacto/callback", { method: "POST", body: JSON.stringify({ code, state }) })
      .then(() => navigate("/integrations", { replace: true }))
      .catch((e) => setError(e.message));
  }, [params, navigate]);

  return (
    <div className="space-y-3">
      <h2 className="font-display text-3xl font-bold">Pacto</h2>
      {error ? (
        <>
          <p className="text-ember">{error}</p>
          <Link to="/integrations" className="text-sand/70 underline">
            Voltar pra Integrações
          </Link>
        </>
      ) : (
        <p className="text-sand/55">Concluindo a conexão e buscando os dados das unidades…</p>
      )}
    </div>
  );
}
