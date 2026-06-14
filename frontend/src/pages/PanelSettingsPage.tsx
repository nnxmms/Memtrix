import { useState } from "react";
import { KeyRound, Save } from "lucide-react";
import { getToken, setToken } from "../api";
import { Card, Field, PageHeader } from "../components/ui";
import { useToast } from "../components/Toast";

export function PanelSettingsPage() {
  const toast = useToast();
  const [token, setTokenValue] = useState(getToken());

  const save = () => {
    setToken(token.trim());
    toast.success(
      token.trim()
        ? "Access token saved to this browser."
        : "Access token cleared."
    );
  };

  return (
    <div>
      <PageHeader
        title="Panel Settings"
        subtitle="Settings for this control panel, stored locally in your browser."
      />

      <Card
        title="Shared-secret token"
        desc="If the backend is started with MEMTRIX_WEB_TOKEN, enter the same value here so requests are authorized. Leave empty if no token is configured."
      >
        <Field label="Access token">
          <div className="input-icon">
            <KeyRound size={16} color="var(--text-muted)" />
            <input
              className="input"
              type="password"
              value={token}
              onChange={(e) => setTokenValue(e.target.value)}
              placeholder="Paste the shared secret"
            />
          </div>
        </Field>
        <div className="btn-row" style={{ marginTop: 8 }}>
          <button className="btn btn-primary" onClick={save}>
            <Save size={16} /> Save token
          </button>
        </div>
      </Card>
    </div>
  );
}
