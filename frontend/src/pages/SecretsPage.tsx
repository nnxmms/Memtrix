import { useEffect, useState } from "react";
import { Eye, EyeOff, KeyRound, Pencil, Save } from "lucide-react";
import { api, ApiError, type SecretInfo } from "../api";
import { Badge, Card, Empty, Field, PageHeader, Spinner } from "../components/ui";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";

export function SecretsPage() {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [backend, setBackend] = useState("");
  const [secrets, setSecrets] = useState<SecretInfo[]>([]);
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<SecretInfo | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.listSecrets();
      setBackend(res.backend);
      setSecrets(res.secrets);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to load secrets.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <PageHeader
        title="Secrets"
        subtitle="Sensitive credentials referenced by your configuration."
      />

      <div className="toolbar">
        <Badge kind="neutral">
          <KeyRound size={14} /> Backend: {backend || "—"}
        </Badge>
      </div>

      {loading ? (
        <Spinner />
      ) : secrets.length === 0 ? (
        <Card>
          <Empty>No managed secrets found.</Empty>
        </Card>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Key</th>
                <th>Value</th>
                <th style={{ width: 110 }}></th>
              </tr>
            </thead>
            <tbody>
              {secrets.map((s) => (
                <tr key={s.key}>
                  <td style={{ fontWeight: 600 }} className="mono">
                    {s.key}
                  </td>
                  <td className="mono">
                    {revealed[s.key] ? s.value || <span className="muted">(empty)</span> : "••••••••••••"}
                  </td>
                  <td>
                    <div className="cell-actions">
                      <button
                        className="btn btn-icon"
                        title={revealed[s.key] ? "Hide" : "Reveal"}
                        onClick={() =>
                          setRevealed((r) => ({ ...r, [s.key]: !r[s.key] }))
                        }
                      >
                        {revealed[s.key] ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                      <button
                        className="btn btn-icon"
                        title="Edit"
                        onClick={() => setEditing(s)}
                      >
                        <Pencil size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <EditSecretModal
          secret={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await load();
          }}
        />
      )}
    </div>
  );
}

function EditSecretModal({
  secret,
  onClose,
  onSaved,
}: {
  secret: SecretInfo;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    setSaving(true);
    try {
      await api.setSecret(secret.key, value);
      toast.success("Secret updated. Restart to apply.");
      onSaved();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to save secret.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      title={`Edit ${secret.key}`}
      desc="Enter a new value. The current value is never pre-filled for safety."
      onClose={onClose}
    >
      <Field label="New value">
        <input
          className="input"
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Enter new secret value"
          autoFocus
        />
      </Field>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={saving || !value}>
          <Save size={16} /> Save secret
        </button>
      </div>
    </Modal>
  );
}
