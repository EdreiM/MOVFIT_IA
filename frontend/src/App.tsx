import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import AiConfigPage from "./pages/AiConfigPage";
import ConversationsPage from "./pages/ConversationsPage";
import LeadsPage from "./pages/LeadsPage";
import IntegrationsPage from "./pages/IntegrationsPage";
import PactoCallbackPage from "./pages/PactoCallbackPage";
import TestChatPage from "./pages/TestChatPage";
import ToolsPage from "./pages/ToolsPage";
import CatalogPage from "./pages/CatalogPage";
import PromotionsPage from "./pages/PromotionsPage";
import AccountPage from "./pages/AccountPage";
import ApiKeysPage from "./pages/ApiKeysPage";
import PublicMetricsPage from "./pages/PublicMetricsPage";
import WebhookLogsPage from "./pages/WebhookLogsPage";

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen grid place-items-center bg-mesh text-lime">
        Carregando…
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      {/* Fora do login de propósito — acesso só com chave de API, sem
          nenhuma permissão de admin do painel. Ver ApiKeysPage. */}
      <Route path="/metrics-view" element={<PublicMetricsPage />} />
      <Route
        path="/*"
        element={
          <PrivateRoute>
            <Layout>
              <Routes>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/numbers" element={<Navigate to="/integrations" replace />} />
                <Route path="/ai" element={<AiConfigPage />} />
                <Route path="/test-chat" element={<TestChatPage />} />
                <Route path="/tools" element={<ToolsPage />} />
                <Route path="/catalog" element={<CatalogPage />} />
                <Route path="/promotions" element={<PromotionsPage />} />
                <Route path="/conversations" element={<ConversationsPage />} />
                <Route path="/leads" element={<LeadsPage />} />
                <Route path="/integrations" element={<IntegrationsPage />} />
                <Route path="/pacto/callback" element={<PactoCallbackPage />} />
                <Route path="/webhook-logs" element={<WebhookLogsPage />} />
                <Route path="/api-keys" element={<ApiKeysPage />} />
                <Route path="/account" element={<AccountPage />} />
              </Routes>
            </Layout>
          </PrivateRoute>
        }
      />
    </Routes>
  );
}
