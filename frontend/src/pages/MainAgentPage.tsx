import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { Card, Field, PageHeader, Spinner } from "../components/ui";
import { useConfig } from "../app/context";
import { useSaveConfig } from "../app/useSaveConfig";

export function MainAgentPage() {
  const { config, loading } = useConfig();
  const { save, saving, errors } = useSaveConfig();
  const [draft, setDraft] = useState<Record<string, any>>({});

  useEffect(() => {
    if (config?.["main-agent"]) setDraft({ ...config["main-agent"] });
  }, [config]);

  if (loading || !config) return <Spinner />;

  const models = Object.keys(config.models ?? {});
  const channels = Object.keys(config.channels ?? {});

  const set = (k: string, v: any) => setDraft((d) => ({ ...d, [k]: v }));

  const onSave = () => save({ ...config, "main-agent": draft });

  return (
    <div>
      <PageHeader title="Main Agent" subtitle="The primary agent users interact with." />

      <Card title="Identity & routing">
        <Field label="Display name">
          <input
            className="input"
            value={draft.name ?? ""}
            onChange={(e) => set("name", e.target.value)}
            placeholder="Memtrix"
          />
        </Field>
        <div className="grid grid-2">
          <Field label="Model">
            <select
              className="select"
              value={draft.model ?? ""}
              onChange={(e) => set("model", e.target.value)}
            >
              <option value="">Select…</option>
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Channel">
            <select
              className="select"
              value={draft.channel ?? ""}
              onChange={(e) => set("channel", e.target.value)}
            >
              <option value="">Select…</option>
              {channels.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div className="btn-row" style={{ marginTop: 8 }}>
          <label className="switch">
            <input
              type="checkbox"
              checked={!!draft.verbose}
              onChange={(e) => set("verbose", e.target.checked)}
            />
            Verbose tool output
          </label>
          <label className="switch" style={{ marginLeft: 24 }}>
            <input
              type="checkbox"
              checked={!!draft.reasoning}
              onChange={(e) => set("reasoning", e.target.checked)}
            />
            Show reasoning
          </label>
        </div>

        {errors.length > 0 && (
          <ul style={{ color: "var(--danger-text)", fontSize: 13, marginTop: 14 }}>
            {errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        )}

        <div className="btn-row" style={{ marginTop: 18 }}>
          <button className="btn btn-primary" onClick={onSave} disabled={saving}>
            <Save size={16} /> Save changes
          </button>
        </div>
      </Card>
    </div>
  );
}
