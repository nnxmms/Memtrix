import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { Card, Field, PageHeader, Spinner } from "../components/ui";
import { useConfig } from "../app/context";
import { useSaveConfig } from "../app/useSaveConfig";

const DEFAULTS: Record<string, any> = {
  enabled: false,
  provider: "local",
  model: "base",
  language: "",
  max_audio_bytes: 25000000,
  timeout_seconds: 180,
};

export function VoicePage() {
  const { config, loading } = useConfig();
  const { save, saving, errors } = useSaveConfig();
  const [draft, setDraft] = useState<Record<string, any>>({});

  useEffect(() => {
    if (config) setDraft({ ...DEFAULTS, ...(config.voice ?? {}) });
  }, [config]);

  if (loading || !config) return <Spinner />;

  const set = (k: string, v: any) => setDraft((d) => ({ ...d, [k]: v }));
  const onSave = () =>
    save({
      ...config,
      voice: {
        ...draft,
        language: draft.language ? String(draft.language).trim() : null,
      },
    });

  return (
    <div>
      <PageHeader
        title="Voice"
        subtitle="Configure local Matrix audio transcription (speech-to-text)."
      />

      <Card title="Transcription">
        <label className="switch" style={{ marginBottom: 12 }}>
          <input
            type="checkbox"
            checked={!!draft.enabled}
            onChange={(e) => set("enabled", e.target.checked)}
          />
          Enable Matrix voice transcription
        </label>

        <div className="grid grid-2">
          <Field label="Provider">
            <select
              className="select"
              value={draft.provider ?? "local"}
              onChange={(e) => set("provider", e.target.value)}
            >
              <option value="local">local</option>
            </select>
          </Field>
          <Field label="Model" hint="faster-whisper model tier">
            <select
              className="select"
              value={draft.model ?? "base"}
              onChange={(e) => set("model", e.target.value)}
            >
              {[
                "tiny",
                "base",
                "small",
                "medium",
                "large-v3",
              ].map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div className="grid grid-3">
          <Field label="Language" hint="Leave empty for auto-detect">
            <input
              className="input"
              value={draft.language ?? ""}
              onChange={(e) => set("language", e.target.value)}
              placeholder="e.g. en"
            />
          </Field>
          <Field label="Max audio bytes">
            <input
              className="input"
              type="number"
              value={draft.max_audio_bytes ?? 0}
              onChange={(e) => set("max_audio_bytes", Number(e.target.value))}
            />
          </Field>
          <Field label="Timeout (seconds)">
            <input
              className="input"
              type="number"
              value={draft.timeout_seconds ?? 0}
              onChange={(e) => set("timeout_seconds", Number(e.target.value))}
            />
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
      </div>
    </div>
  );
}
