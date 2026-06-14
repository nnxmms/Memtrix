import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { Card, Field, PageHeader, Spinner } from "../components/ui";
import { useConfig } from "../app/context";
import { useSaveConfig } from "../app/useSaveConfig";

const DEFAULTS: Record<string, any> = {
  backend: "native",
  recall_mode: "hybrid",
  write_frequency: "async",
  reasoning_level: "low",
  reasoning_model: "",
  batch_tokens: 1000,
  peer_card_max_chars: 1500,
  dual_peer: true,
  inject_top_k: 5,
};

export function MemoryPage() {
  const { config, loading } = useConfig();
  const { save, saving, errors } = useSaveConfig();
  const [draft, setDraft] = useState<Record<string, any>>({});

  useEffect(() => {
    if (config) setDraft({ ...DEFAULTS, ...(config.memory ?? {}) });
  }, [config]);

  if (loading || !config) return <Spinner />;

  const models = Object.keys(config.models ?? {});
  const set = (k: string, v: any) => setDraft((d) => ({ ...d, [k]: v }));
  const onSave = () => save({ ...config, memory: draft });

  return (
    <div>
      <PageHeader
        title="Memory"
        subtitle="Tune how the agent forms, recalls, and reasons over long-term memory."
      />

      <Card title="Recall & writing">
        <div className="grid grid-2">
          <Field label="Backend" hint="native enables the memory system; off disables it.">
            <select className="select" value={draft.backend} onChange={(e) => set("backend", e.target.value)}>
              <option value="native">native</option>
              <option value="off">off</option>
            </select>
          </Field>
          <Field label="Recall mode">
            <select className="select" value={draft.recall_mode} onChange={(e) => set("recall_mode", e.target.value)}>
              <option value="hybrid">hybrid</option>
              <option value="context">context</option>
              <option value="tools">tools</option>
              <option value="off">off</option>
            </select>
          </Field>
          <Field label="Write frequency">
            <select className="select" value={draft.write_frequency} onChange={(e) => set("write_frequency", e.target.value)}>
              <option value="async">async</option>
              <option value="turn">turn</option>
            </select>
          </Field>
          <Field label="Reasoning level">
            <select className="select" value={draft.reasoning_level} onChange={(e) => set("reasoning_level", e.target.value)}>
              {["minimal", "low", "medium", "high", "max"].map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Card>

      <Card title="Reasoning & tuning">
        <Field label="Reasoning model" hint="Leave empty to reuse the main agent's model.">
          <select
            className="select"
            value={draft.reasoning_model ?? ""}
            onChange={(e) => set("reasoning_model", e.target.value || null)}
          >
            <option value="">(main model)</option>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </Field>
        <div className="grid grid-3">
          <Field label="Batch tokens">
            <input
              className="input"
              type="number"
              value={draft.batch_tokens ?? 0}
              onChange={(e) => set("batch_tokens", Number(e.target.value))}
            />
          </Field>
          <Field label="Peer-card max chars">
            <input
              className="input"
              type="number"
              value={draft.peer_card_max_chars ?? 0}
              onChange={(e) => set("peer_card_max_chars", Number(e.target.value))}
            />
          </Field>
          <Field label="Inject top-K">
            <input
              className="input"
              type="number"
              value={draft.inject_top_k ?? 0}
              onChange={(e) => set("inject_top_k", Number(e.target.value))}
            />
          </Field>
        </div>
        <label className="switch" style={{ marginTop: 6 }}>
          <input
            type="checkbox"
            checked={!!draft.dual_peer}
            onChange={(e) => set("dual_peer", e.target.checked)}
          />
          Maintain a separate memory card for the agent (dual-peer)
        </label>
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
