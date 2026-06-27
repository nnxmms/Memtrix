import { useEffect, useMemo, useState } from "react";
import { Bot, Plus, Trash2 } from "lucide-react";
import { api, ApiError, type AgentMeta } from "../api";
import { Badge, Card, Empty, Field, PageHeader, Spinner } from "../components/ui";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { useConfig } from "../app/context";

interface AgentEntry {
  display_name?: string;
  description?: string;
  model?: string;
  matrix_user_id?: string;
}

export function AgentsPage() {
  const { config, reload } = useConfig();
  const toast = useToast();

  const [meta, setMeta] = useState<AgentMeta | null>(null);
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    api
      .agentMeta()
      .then(setMeta)
      .catch(() => setMeta(null));
  }, []);

  const agents: Record<string, AgentEntry> = (config?.agents as any) ?? {};
  const slugs = Object.keys(agents);
  const models = useMemo(() => Object.keys((config?.models as any) ?? {}), [config]);

  const handleDelete = async (slug: string) => {
    setDeleting(true);
    try {
      const res = await api.deleteAgent(slug);
      toast.success(res.message);
      await reload();
      setConfirmDelete(null);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to delete sub-agent.");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Sub-Agents"
        subtitle="Specialist agents, each with its own Matrix identity, workspace, memory, and persona."
      />

      <div className="toolbar">
        <button
          className="btn btn-primary btn-sm"
          onClick={() => setCreating(true)}
          disabled={!config || !meta}
        >
          <Plus size={15} /> Create sub-agent
        </button>
      </div>

      {slugs.length === 0 ? (
        <Card>
          <Empty>
            No sub-agents yet. Create one here, or just ask the agent in chat
            (e.g. "create a sub-agent named Dennis, a baking specialist").
          </Empty>
        </Card>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Agent</th>
                <th>Model</th>
                <th>Matrix user</th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {slugs.map((slug) => {
                const a = agents[slug];
                return (
                  <tr key={slug}>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Bot size={16} className="muted" />
                        <div>
                          <div style={{ fontWeight: 600 }}>{a.display_name ?? slug}</div>
                          {a.description && (
                            <div className="muted" style={{ fontSize: "0.85em" }}>
                              {a.description}
                            </div>
                          )}
                        </div>
                      </div>
                    </td>
                    <td>
                      <Badge kind="accent">{a.model ?? "—"}</Badge>
                    </td>
                    <td className="muted mono">{a.matrix_user_id ?? "—"}</td>
                    <td>
                      <button
                        className="btn btn-icon danger"
                        title="Delete"
                        onClick={() => setConfirmDelete(slug)}
                      >
                        <Trash2 size={16} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {creating && meta && (
        <CreateAgentModal
          meta={meta}
          models={models}
          defaultModel={(config?.["main-agent"] as any)?.model ?? models[0] ?? ""}
          onClose={() => setCreating(false)}
          onCreated={async () => {
            setCreating(false);
            await reload();
          }}
        />
      )}

      <Modal
        open={confirmDelete !== null}
        title="Delete sub-agent?"
        desc={
          `This removes "${confirmDelete}" and its workspace, memory, and sessions. ` +
          "The Matrix account itself stays on the homeserver. Restart to apply."
        }
        onClose={() => setConfirmDelete(null)}
      >
        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={() => setConfirmDelete(null)}>
            Cancel
          </button>
          <button
            className="btn btn-danger"
            disabled={deleting}
            onClick={() => confirmDelete && handleDelete(confirmDelete)}
          >
            <Trash2 size={16} /> {deleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </Modal>
    </div>
  );
}

function CreateAgentModal({
  meta,
  models,
  defaultModel,
  onClose,
  onCreated,
}: {
  meta: AgentMeta;
  models: string[];
  defaultModel: string;
  onClose: () => void;
  onCreated: () => Promise<void>;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState(defaultModel);
  const [userId, setUserId] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    setError("");
    if (!name.trim()) {
      setError("A name is required.");
      return;
    }
    if (!description.trim()) {
      setError("A description of the agent's expertise is required.");
      return;
    }
    if (!meta.managed && (!userId.trim() || !accessToken.trim())) {
      setError("This external homeserver needs a Matrix user ID and access token.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await api.createAgent({
        name: name.trim(),
        description: description.trim(),
        model,
        matrix_user_id: userId.trim(),
        matrix_access_token: accessToken.trim(),
      });
      toast.success(res.message);
      await onCreated();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create sub-agent.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open
      title="Create sub-agent"
      desc="A new specialist agent with its own Matrix identity, workspace, and memory."
      onClose={onClose}
    >
      <Field label="Name" hint="A real human name — becomes the agent's identity (e.g. Dennis).">
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Dennis"
          autoFocus
        />
      </Field>

      <Field label="Expertise" hint="What this agent specializes in.">
        <textarea
          className="textarea"
          rows={3}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Baking and pastry specialist — recipes, techniques, troubleshooting"
        />
      </Field>

      <Field label="Model" hint="Defaults to the main agent's model.">
        <select className="select" value={model} onChange={(e) => setModel(e.target.value)}>
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </Field>

      {meta.managed ? (
        <p className="muted" style={{ fontSize: "0.85em", marginTop: -4 }}>
          A Matrix account will be created automatically on the bundled homeserver
          (<span className="mono">{meta.server_name}</span>).
        </p>
      ) : (
        <>
          <p className="muted" style={{ fontSize: "0.85em" }}>
            This deployment uses an external homeserver (
            <span className="mono">{meta.server_name}</span>), which can't create
            accounts automatically. Pre-create a Matrix account for this agent and
            provide its credentials below.
          </p>
          <Field label="Matrix user ID" hint="e.g. @dennis:matrix.org">
            <input
              className="input"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="@dennis:matrix.org"
            />
          </Field>
          <Field label="Access token">
            <input
              className="input"
              type="password"
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
              placeholder="syt_…"
            />
          </Field>
        </>
      )}

      {error && <div className="field-error">{error}</div>}

      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={submitting}>
          {submitting ? <Spinner /> : <Plus size={16} />}
          {submitting ? "Creating…" : "Create"}
        </button>
      </div>
    </Modal>
  );
}
