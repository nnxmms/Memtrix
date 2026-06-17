import { Route, Routes } from "react-router-dom";
import { Sidebar, Topbar } from "./app/Layout";
import { DashboardPage } from "./pages/DashboardPage";
import { MainAgentPage } from "./pages/MainAgentPage";
import {
  AgentsPage,
  ChannelsPage,
  ModelsPage,
  ProvidersPage,
} from "./pages/configPages";
import { MemoryPage } from "./pages/MemoryPage";
import { VoicePage } from "./pages/VoicePage";
import { SecretsPage } from "./pages/SecretsPage";
import { MemoryAdminPage } from "./pages/MemoryAdminPage";
import { PanelSettingsPage } from "./pages/PanelSettingsPage";

export function App() {
  return (
    <div className="layout">
      <Sidebar />
      <div className="main">
        <Topbar />
        <main className="content">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/main-agent" element={<MainAgentPage />} />
            <Route path="/providers" element={<ProvidersPage />} />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/channels" element={<ChannelsPage />} />
            <Route path="/agents" element={<AgentsPage />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/voice" element={<VoicePage />} />
            <Route path="/secrets" element={<SecretsPage />} />
            <Route path="/memory-admin" element={<MemoryAdminPage />} />
            <Route path="/settings" element={<PanelSettingsPage />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
