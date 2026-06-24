import { useEffect, useState } from "react";
import { Save, Plug } from "lucide-react";
import { Card, Field, PageHeader, Spinner } from "../components/ui";
import { useConfig } from "../app/context";
import { useSaveConfig } from "../app/useSaveConfig";
import { useToast } from "../components/Toast";
import { api } from "../api";

const PASSWORD_PLACEHOLDER = "$EMAIL_PASSWORD";

const DEFAULTS: Record<string, any> = {
  enabled: false,
  imap_host: "",
  imap_port: 993,
  imap_ssl: true,
  smtp_host: "",
  smtp_port: 587,
  smtp_security: "starttls",
  username: "",
  from_address: "",
  password: PASSWORD_PLACEHOLDER,
  mailbox: "INBOX",
  auto_mark_read: true,
  max_fetch: 10,
  max_body_chars: 4000,
};

export function EmailPage() {
  const { config, loading } = useConfig();
  const { save, saving, errors } = useSaveConfig();
  const toast = useToast();
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [password, setPassword] = useState("");
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    if (config) setDraft({ ...DEFAULTS, ...(config.email ?? {}) });
  }, [config]);

  if (loading || !config) return <Spinner />;

  const agentName = String((config as any)?.["main-agent"]?.name ?? "Memtrix");
  const set = (k: string, v: any) => setDraft((d) => ({ ...d, [k]: v }));

  const buildSection = () => ({
    ...draft,
    password: PASSWORD_PLACEHOLDER,
    from_address: draft.from_address ? String(draft.from_address).trim() : "",
  });

  const onSave = async () => {
    // Persist the password (if entered) to the secret store, never to config.json.
    if (password.trim()) {
      try {
        await api.setSecret("EMAIL_PASSWORD", password.trim());
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Failed to save password secret.");
        return;
      }
    }
    const ok = await save({ ...config, email: buildSection() });
    if (ok) setPassword("");
  };

  const onTest = async () => {
    setTesting(true);
    try {
      const params = {
        ...buildSection(),
        // Use the freshly typed password if present, otherwise the saved secret.
        password: password.trim() || PASSWORD_PLACEHOLDER,
      };
      const res = await api.testEmail(params);
      if (res.ok) toast.success(res.detail);
      else toast.error(res.detail);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Test failed.");
    } finally {
      setTesting(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Email"
        subtitle="Let Memtrix read your mailbox over IMAP and send mail over SMTP."
      />

      <Card title="Mailbox">
        <label className="switch" style={{ marginBottom: 12 }}>
          <input
            type="checkbox"
            checked={!!draft.enabled}
            onChange={(e) => set("enabled", e.target.checked)}
          />
          Enable email (check, send, and mark messages read/unread)
        </label>

        <div className="grid grid-2">
          <Field label="Username" hint="Usually the full email address">
            <input
              className="input"
              value={draft.username ?? ""}
              onChange={(e) => set("username", e.target.value)}
              placeholder="you@example.com"
            />
          </Field>
          <Field label="From address" hint="Leave empty to use the username">
            <input
              className="input"
              value={draft.from_address ?? ""}
              onChange={(e) => set("from_address", e.target.value)}
              placeholder="you@example.com"
            />
          </Field>
        </div>

        <div className="grid grid-2">
          <Field label="Display name (Anzeigename)" hint={`Shown as the sender name. Leave empty to use the agent name (${agentName}).`}>
            <input
              className="input"
              value={draft.from_name ?? ""}
              onChange={(e) => set("from_name", e.target.value)}
              placeholder={agentName}
            />
          </Field>
        </div>

        <div className="grid grid-2">
          <Field
            label="Password"
            hint="Stored as the EMAIL_PASSWORD secret (.env / Bitwarden). Leave blank to keep the current value."
          >
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete="new-password"
            />
          </Field>
          <Field label="Mailbox" hint="IMAP folder to read">
            <input
              className="input"
              value={draft.mailbox ?? "INBOX"}
              onChange={(e) => set("mailbox", e.target.value)}
              placeholder="INBOX"
            />
          </Field>
        </div>
      </Card>

      <Card title="Incoming (IMAP)">
        <div className="grid grid-3">
          <Field label="IMAP host">
            <input
              className="input"
              value={draft.imap_host ?? ""}
              onChange={(e) => set("imap_host", e.target.value)}
              placeholder="imap.example.com"
            />
          </Field>
          <Field label="IMAP port">
            <input
              className="input"
              type="number"
              value={draft.imap_port ?? 993}
              onChange={(e) => set("imap_port", Number(e.target.value))}
            />
          </Field>
          <Field label="Encryption">
            <select
              className="select"
              value={draft.imap_ssl ? "ssl" : "starttls"}
              onChange={(e) => set("imap_ssl", e.target.value === "ssl")}
            >
              <option value="ssl">SSL/TLS (993)</option>
              <option value="starttls">STARTTLS</option>
            </select>
          </Field>
        </div>

        <label className="switch" style={{ marginTop: 8 }}>
          <input
            type="checkbox"
            checked={draft.auto_mark_read ?? true}
            onChange={(e) => set("auto_mark_read", e.target.checked)}
          />
          Automatically mark messages as read after they are retrieved
        </label>

        <div className="grid grid-2" style={{ marginTop: 8 }}>
          <Field label="Default messages per check">
            <input
              className="input"
              type="number"
              value={draft.max_fetch ?? 10}
              onChange={(e) => set("max_fetch", Number(e.target.value))}
            />
          </Field>
          <Field label="Max body characters">
            <input
              className="input"
              type="number"
              value={draft.max_body_chars ?? 4000}
              onChange={(e) => set("max_body_chars", Number(e.target.value))}
            />
          </Field>
        </div>
      </Card>

      <Card title="Outgoing (SMTP)">
        <div className="grid grid-3">
          <Field label="SMTP host">
            <input
              className="input"
              value={draft.smtp_host ?? ""}
              onChange={(e) => set("smtp_host", e.target.value)}
              placeholder="smtp.example.com"
            />
          </Field>
          <Field label="SMTP port">
            <input
              className="input"
              type="number"
              value={draft.smtp_port ?? 587}
              onChange={(e) => set("smtp_port", Number(e.target.value))}
            />
          </Field>
          <Field label="Security">
            <select
              className="select"
              value={draft.smtp_security ?? "starttls"}
              onChange={(e) => set("smtp_security", e.target.value)}
            >
              <option value="starttls">STARTTLS (587)</option>
              <option value="ssl">SSL/TLS (465)</option>
              <option value="none">None</option>
            </select>
          </Field>
        </div>
      </Card>

      {errors.length > 0 && (
        <Card>
          <ul style={{ color: "var(--danger-text)", fontSize: 13, margin: 0 }}>
            {errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </Card>
      )}

      <div className="btn-row">
        <button className="btn btn-primary" onClick={onSave} disabled={saving}>
          <Save size={16} /> Save changes
        </button>
        <button className="btn btn-secondary" onClick={onTest} disabled={testing}>
          <Plug size={16} /> {testing ? "Testing…" : "Test connection"}
        </button>
      </div>
    </div>
  );
}
