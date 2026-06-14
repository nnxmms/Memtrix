import { useMemo, useState } from "react";
import { Pencil, Plus, Trash2, Zap } from "lucide-react";
import { api, ApiError, type Config } from "../api";
import { Badge, Card, Empty, Field, PageHeader } from "../components/ui";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { useConfig } from "../app/context";
import { useSaveConfig } from "../app/useSaveConfig";

export interface FieldSpec {
  name: string;
  label: string;
  hint?: string;
  secret?: boolean;
  // When set, this field is only shown for the given discriminator value
  onlyForType?: string;
  // A select whose options derive from another config section
  optionsFrom?: string;
}

export interface ResourceSpec {
  sectionKey: string;
  title: string;
  subtitle: string;
  noun: string;
  // Discriminator field name (e.g. "type") and its allowed values, if any
  typeField?: string;
  typeOptions?: string[];
  fields: FieldSpec[];
  // Which test endpoint to call, if any
  test?: "provider" | "channel";
}

type Instance = Record<string, any>;

export function ResourcePage({ spec }: { spec: ResourceSpec }) {
  const { config } = useConfig();
  const { save, saving } = useSaveConfig();
  const toast = useToast();

  const section: Record<string, Instance> = (config?.[spec.sectionKey] as any) ?? {};
  const names = Object.keys(section);

  const [editing, setEditing] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const persist = async (next: Record<string, Instance>): Promise<boolean> => {
    if (!config) return false;
    return await save({ ...config, [spec.sectionKey]: next });
  };

  const handleDelete = async (name: string) => {
    const next = { ...section };
    delete next[name];
    await persist(next);
    setConfirmDelete(null);
  };

  return (
    <div>
      <PageHeader title={spec.title} subtitle={spec.subtitle} />

      <div className="toolbar">
        <button
          className="btn btn-primary btn-sm"
          onClick={() => setCreating(true)}
          disabled={saving}
        >
          <Plus size={15} /> Add {spec.noun}
        </button>
      </div>

      {names.length === 0 ? (
        <Card>
          <Empty>No {spec.noun}s configured yet.</Empty>
        </Card>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                {spec.typeField && <th>Type</th>}
                <th>Details</th>
                <th style={{ width: 120 }}></th>
              </tr>
            </thead>
            <tbody>
              {names.map((name) => (
                <tr key={name}>
                  <td style={{ fontWeight: 600 }}>{name}</td>
                  {spec.typeField && (
                    <td>
                      <Badge kind="accent">{section[name][spec.typeField] ?? "—"}</Badge>
                    </td>
                  )}
                  <td className="muted mono">{summarize(spec, section[name])}</td>
                  <td>
                    <div className="cell-actions">
                      <button
                        className="btn btn-icon"
                        title="Edit"
                        onClick={() => setEditing(name)}
                      >
                        <Pencil size={16} />
                      </button>
                      <button
                        className="btn btn-icon danger"
                        title="Delete"
                        onClick={() => setConfirmDelete(name)}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(creating || editing) && (
        <InstanceModal
          spec={spec}
          config={config}
          existingNames={names}
          name={editing}
          initial={editing ? section[editing] : {}}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSave={async (name, instance) => {
            const next = { ...section };
            if (editing && editing !== name) delete next[editing];
            next[name] = instance;
            const ok = await persist(next);
            if (ok !== false) {
              setCreating(false);
              setEditing(null);
            }
          }}
          onTest={
            spec.test
              ? async (instance) => {
                  try {
                    const fn =
                      spec.test === "provider" ? api.testProvider : api.testChannel;
                    const { type, ...params } = instance;
                    const res = await fn(type, params);
                    if (res.ok) toast.success(res.detail);
                    else toast.error(res.detail);
                  } catch (e) {
                    toast.error(
                      e instanceof ApiError ? e.message : "Connection test failed."
                    );
                  }
                }
              : undefined
          }
        />
      )}

      <Modal
        open={confirmDelete !== null}
        title={`Delete ${spec.noun}?`}
        desc={`This removes "${confirmDelete}" from the configuration. Restart to apply.`}
        onClose={() => setConfirmDelete(null)}
      >
        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={() => setConfirmDelete(null)}>
            Cancel
          </button>
          <button
            className="btn btn-danger"
            onClick={() => confirmDelete && handleDelete(confirmDelete)}
          >
            <Trash2 size={16} /> Delete
          </button>
        </div>
      </Modal>
    </div>
  );
}

function summarize(spec: ResourceSpec, instance: Instance): string {
  return spec.fields
    .filter((f) => !f.onlyForType || f.onlyForType === instance[spec.typeField ?? ""])
    .map((f) => {
      const v = instance[f.name];
      if (v === undefined || v === "") return null;
      return f.secret ? `${f.name}=••••` : `${f.name}=${v}`;
    })
    .filter(Boolean)
    .join("  ");
}

function InstanceModal({
  spec,
  config,
  existingNames,
  name,
  initial,
  onClose,
  onSave,
  onTest,
}: {
  spec: ResourceSpec;
  config: Config | null;
  existingNames: string[];
  name: string | null;
  initial: Instance;
  onClose: () => void;
  onSave: (name: string, instance: Instance) => Promise<void>;
  onTest?: (instance: Instance) => Promise<void>;
}) {
  const [instanceName, setInstanceName] = useState(name ?? "");
  const [type, setType] = useState<string>(
    spec.typeField ? initial[spec.typeField] ?? spec.typeOptions?.[0] ?? "" : ""
  );
  const [values, setValues] = useState<Instance>({ ...initial });
  const [nameError, setNameError] = useState<string>("");

  const visibleFields = useMemo(
    () => spec.fields.filter((f) => !f.onlyForType || f.onlyForType === type),
    [spec.fields, type]
  );

  const buildInstance = (): Instance => {
    const out: Instance = { ...initial };
    if (spec.typeField) out[spec.typeField] = type;
    // Strip fields that no longer apply to the chosen type
    spec.fields.forEach((f) => {
      if (f.onlyForType && f.onlyForType !== type) delete out[f.name];
    });
    visibleFields.forEach((f) => {
      out[f.name] = values[f.name] ?? "";
    });
    return out;
  };

  const submit = async () => {
    const trimmed = instanceName.trim();
    if (!trimmed) {
      setNameError("Name is required.");
      return;
    }
    if (trimmed !== name && existingNames.includes(trimmed)) {
      setNameError("A resource with this name already exists.");
      return;
    }
    await onSave(trimmed, buildInstance());
  };

  return (
    <Modal
      open
      title={name ? `Edit ${spec.noun}` : `Add ${spec.noun}`}
      desc={`Configure a ${spec.noun}. Changes apply after a restart.`}
      onClose={onClose}
    >
      <Field label="Instance name" error={nameError}>
        <input
          className={`input${nameError ? " invalid" : ""}`}
          value={instanceName}
          onChange={(e) => setInstanceName(e.target.value)}
          placeholder={`my-${spec.noun}`}
        />
      </Field>

      {spec.typeField && spec.typeOptions && (
        <Field label="Type">
          <select className="select" value={type} onChange={(e) => setType(e.target.value)}>
            {spec.typeOptions.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </Field>
      )}

      {visibleFields.map((f) => (
        <Field key={f.name} label={f.label} hint={f.hint}>
          {f.optionsFrom ? (
            <select
              className="select"
              value={values[f.name] ?? ""}
              onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
            >
              <option value="">Select…</option>
              {Object.keys((config?.[f.optionsFrom] as any) ?? {}).map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="input"
              type={f.secret ? "password" : "text"}
              value={values[f.name] ?? ""}
              onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
              placeholder={f.secret ? "value or $PLACEHOLDER" : ""}
            />
          )}
        </Field>
      ))}

      <div className="modal-actions">
        {onTest && (
          <button
            className="btn btn-secondary"
            style={{ marginRight: "auto" }}
            onClick={() => onTest(buildInstance())}
          >
            <Zap size={16} /> Test connection
          </button>
        )}
        <button className="btn btn-secondary" onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={submit}>
          Save
        </button>
      </div>
    </Modal>
  );
}
