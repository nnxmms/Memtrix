import { useCallback, useEffect, useState } from "react";
import {
  CalendarClock,
  Download,
  Pause,
  Pencil,
  Play,
  Plus,
  Repeat,
  Search,
  Snowflake,
  Trash2,
  Upload,
  Users,
} from "lucide-react";
import {
  api,
  ApiError,
  type Conclusion,
  type MemoryEvent,
  type PeerCard,
  type PeerSummary,
  type PersonCard,
  type PersonSummary,
} from "../api";
import { Badge, Card, Empty, Field, PageHeader, Spinner } from "../components/ui";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";

type Section = "memory" | "people" | "events";

export function MemoryAdminPage() {
  const toast = useToast();
  const [peers, setPeers] = useState<PeerSummary[]>([]);
  const [activePeer, setActivePeer] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [deriverPaused, setDeriverPaused] = useState(false);
  const [section, setSection] = useState<Section>("memory");

  const loadPeers = useCallback(async () => {
    try {
      const [list, deriver] = await Promise.all([api.listPeers(), api.getDeriver()]);
      setPeers(list);
      setDeriverPaused(deriver.paused);
      setActivePeer((cur) => cur || list[0]?.peer || "");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to load memory.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadPeers();
  }, [loadPeers]);

  const toggleDeriver = async () => {
    try {
      const res = await api.setDeriver(!deriverPaused);
      setDeriverPaused(res.paused);
      toast.success(res.paused ? "Background reasoning paused." : "Background reasoning resumed.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to toggle deriver.");
    }
  };

  const doExport = async () => {
    try {
      const records = await api.exportMemory();
      const blob = new Blob([JSON.stringify(records, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "memtrix-memory-export.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Export failed.");
    }
  };

  const doImport = async (file: File) => {
    try {
      const records = JSON.parse(await file.text());
      if (!Array.isArray(records)) throw new Error("File must contain a JSON array.");
      const res = await api.importMemory(records);
      toast.success(res.message);
      await loadPeers();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Import failed.");
    }
  };

  if (loading) return <Spinner />;

  return (
    <div>
      <PageHeader
        title="Memory Admin"
        subtitle="Inspect and curate the agent's reasoning memory, people, and events."
      />

      <div className="tabs" style={{ marginBottom: 18 }}>
        <button
          className={`tab${section === "memory" ? " active" : ""}`}
          onClick={() => setSection("memory")}
        >
          <Search size={14} /> Memory
        </button>
        <button
          className={`tab${section === "people" ? " active" : ""}`}
          onClick={() => setSection("people")}
        >
          <Users size={14} /> People
        </button>
        <button
          className={`tab${section === "events" ? " active" : ""}`}
          onClick={() => setSection("events")}
        >
          <CalendarClock size={14} /> Events
        </button>
      </div>

      {section === "people" && <PeoplePanel />}
      {section === "events" && <EventsPanel />}
      {section === "memory" && (
        <>
          <div className="toolbar">
            <button className="btn btn-secondary btn-sm" onClick={toggleDeriver}>
              {deriverPaused ? <Play size={15} /> : <Pause size={15} />}
              {deriverPaused ? "Resume reasoning" : "Pause reasoning"}
            </button>
            <button className="btn btn-secondary btn-sm" onClick={doExport}>
              <Download size={15} /> Export
            </button>
            <label className="btn btn-secondary btn-sm" style={{ cursor: "pointer" }}>
              <Upload size={15} /> Import
              <input
                type="file"
                accept="application/json"
                style={{ display: "none" }}
                onChange={(e) => e.target.files?.[0] && doImport(e.target.files[0])}
              />
            </label>
            {deriverPaused && <Badge kind="warning">Reasoning paused</Badge>}
          </div>

          {peers.length === 0 ? (
            <Card>
              <Empty>No peers in memory yet.</Empty>
            </Card>
          ) : (
            <>
              <div className="tabs">
                {peers.map((p) => (
                  <button
                    key={p.peer}
                    className={`tab${activePeer === p.peer ? " active" : ""}`}
                    onClick={() => setActivePeer(p.peer)}
                  >
                    {p.peer}
                    <Badge kind="neutral">{p.count}</Badge>
                    {p.frozen && <Snowflake size={13} color="var(--accent)" />}
                  </button>
                ))}
              </div>

              {activePeer && <PeerPanel peer={activePeer} onChanged={loadPeers} />}
            </>
          )}
        </>
      )}
    </div>
  );
}

function PeerPanel({ peer, onChanged }: { peer: string; onChanged: () => void }) {
  const toast = useToast();
  const [card, setCard] = useState<PeerCard | null>(null);
  const [conclusions, setConclusions] = useState<Conclusion[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Conclusion | null>(null);
  const [adding, setAdding] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<Conclusion | null>(null);
  const [confirmWipe, setConfirmWipe] = useState(false);

  const loadData = useCallback(
    async (q = "") => {
      setLoading(true);
      try {
        const [c, list] = await Promise.all([
          api.getCard(peer),
          api.listConclusions({ peer, q: q || undefined, limit: 200 }),
        ]);
        setCard(c);
        setConclusions(list);
      } catch (e) {
        toast.error(e instanceof ApiError ? e.message : "Failed to load peer data.");
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [peer]
  );

  useEffect(() => {
    setQuery("");
    loadData();
  }, [loadData]);

  const toggleFreeze = async () => {
    if (!card) return;
    try {
      await api.setFreeze(peer, !card.frozen);
      toast.success(!card.frozen ? "Peer card frozen." : "Peer card unfrozen.");
      await loadData(query);
      onChanged();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to toggle freeze.");
    }
  };

  const saveCard = async (text: string) => {
    try {
      await api.putCard(peer, text);
      toast.success("Peer card saved.");
      await loadData(query);
      onChanged();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to save card.");
    }
  };

  const deleteOne = async (c: Conclusion) => {
    try {
      await api.deleteConclusion(c.id);
      toast.success("Conclusion deleted.");
      setConfirmDelete(null);
      await loadData(query);
      onChanged();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Delete failed.");
    }
  };

  const wipe = async () => {
    try {
      const res = await api.wipePeer(peer);
      toast.success(res.message);
      setConfirmWipe(false);
      await loadData();
      onChanged();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Wipe failed.");
    }
  };

  if (loading) return <Spinner />;

  return (
    <>
      {card && <PeerCardEditor card={card} onSave={saveCard} onToggleFreeze={toggleFreeze} />}

      <Card
        title="Conclusions"
        desc="Individual reasoning records derived from conversations."
      >
        <div className="row-between" style={{ marginBottom: 14, gap: 12 }}>
          <div className="search-box">
            <Search size={15} color="var(--text-muted)" />
            <input
              className="input"
              placeholder="Semantic search…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && loadData(query)}
            />
          </div>
          <div className="btn-row">
            <button className="btn btn-secondary btn-sm" onClick={() => setAdding(true)}>
              <Plus size={15} /> Add
            </button>
            <button className="btn btn-danger btn-sm" onClick={() => setConfirmWipe(true)}>
              <Trash2 size={15} /> Wipe peer
            </button>
          </div>
        </div>

        {conclusions.length === 0 ? (
          <Empty>No conclusions{query ? " match your search" : ""}.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 110 }}>Kind</th>
                  <th>Content</th>
                  <th style={{ width: 80 }}>Source</th>
                  <th style={{ width: 90 }}></th>
                </tr>
              </thead>
              <tbody>
                {conclusions.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <Badge kind="accent">{c.kind}</Badge>
                    </td>
                    <td>{c.content}</td>
                    <td>
                      <Badge kind={c.source === "manual" ? "warning" : "neutral"}>
                        {c.source}
                      </Badge>
                    </td>
                    <td>
                      <div className="cell-actions">
                        <button
                          className="btn btn-icon"
                          title="Edit"
                          onClick={() => setEditing(c)}
                        >
                          <Pencil size={16} />
                        </button>
                        <button
                          className="btn btn-icon danger"
                          title="Delete"
                          onClick={() => setConfirmDelete(c)}
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
      </Card>

      {(editing || adding) && (
        <ConclusionModal
          peer={peer}
          conclusion={editing}
          onClose={() => {
            setEditing(null);
            setAdding(false);
          }}
          onSaved={async () => {
            setEditing(null);
            setAdding(false);
            await loadData(query);
            onChanged();
          }}
        />
      )}

      <Modal
        open={confirmDelete !== null}
        title="Delete conclusion?"
        desc="This permanently removes the record from memory."
        onClose={() => setConfirmDelete(null)}
      >
        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={() => setConfirmDelete(null)}>
            Cancel
          </button>
          <button className="btn btn-danger" onClick={() => confirmDelete && deleteOne(confirmDelete)}>
            <Trash2 size={16} /> Delete
          </button>
        </div>
      </Modal>

      <Modal
        open={confirmWipe}
        title={`Wipe all memory for ${peer}?`}
        desc="This permanently deletes every conclusion for this peer. The peer card is not affected."
        onClose={() => setConfirmWipe(false)}
      >
        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={() => setConfirmWipe(false)}>
            Cancel
          </button>
          <button className="btn btn-danger" onClick={wipe}>
            <Trash2 size={16} /> Wipe everything
          </button>
        </div>
      </Modal>
    </>
  );
}

function PeerCardEditor({
  card,
  onSave,
  onToggleFreeze,
}: {
  card: PeerCard;
  onSave: (text: string) => void;
  onToggleFreeze: () => void;
}) {
  const [text, setText] = useState(card.text);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setText(card.text);
    setDirty(false);
  }, [card]);

  return (
    <Card
      title="Peer card"
      desc={`The agent's working summary of this peer (${text.length}/${card.max_chars} chars).`}
    >
      <textarea
        className="textarea mono"
        rows={8}
        value={text}
        maxLength={card.max_chars}
        onChange={(e) => {
          setText(e.target.value);
          setDirty(true);
        }}
      />
      <div className="row-between" style={{ marginTop: 14 }}>
        <button
          className={`btn btn-sm ${card.frozen ? "btn-primary" : "btn-secondary"}`}
          onClick={onToggleFreeze}
        >
          <Snowflake size={15} /> {card.frozen ? "Frozen — unfreeze" : "Freeze card"}
        </button>
        <button
          className="btn btn-primary btn-sm"
          disabled={!dirty}
          onClick={() => onSave(text)}
        >
          Save card
        </button>
      </div>
    </Card>
  );
}

const KINDS = ["fact", "preference", "trait", "goal", "skill", "relationship", "event", "other"];

function ConclusionModal({
  peer,
  conclusion,
  onClose,
  onSaved,
}: {
  peer: string;
  conclusion: Conclusion | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [content, setContent] = useState(conclusion?.content ?? "");
  const [kind, setKind] = useState(conclusion?.kind ?? KINDS[0]);
  const [premises, setPremises] = useState((conclusion?.premises ?? []).join("\n"));
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!content.trim()) return;
    setSaving(true);
    const premiseList = premises
      .split("\n")
      .map((p) => p.trim())
      .filter(Boolean);
    try {
      if (conclusion) {
        await api.updateConclusion(conclusion.id, { content, kind, premises: premiseList });
        toast.success("Conclusion updated.");
      } else {
        await api.addConclusion({ peer, kind, content, premises: premiseList });
        toast.success("Conclusion added.");
      }
      onSaved();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      title={conclusion ? "Edit conclusion" : "Add conclusion"}
      desc="Manually authored records are kept verbatim and skip de-duplication."
      onClose={onClose}
    >
      <Field label="Kind">
        <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Content">
        <textarea
          className="textarea"
          rows={3}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="A concise statement about the peer."
        />
      </Field>
      <Field label="Premises" hint="Optional supporting evidence, one per line.">
        <textarea
          className="textarea"
          rows={3}
          value={premises}
          onChange={(e) => setPremises(e.target.value)}
        />
      </Field>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={saving || !content.trim()}>
          Save
        </button>
      </div>
    </Modal>
  );
}

function PeoplePanel() {
  const toast = useToast();
  const [people, setPeople] = useState<PersonSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<PersonCard | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<PersonSummary | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPeople(await api.listPeople());
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to load people.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openPerson = async (slug: string) => {
    try {
      setSelected(await api.getPerson(slug));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to load person.");
    }
  };

  const remove = async (slug: string) => {
    try {
      const res = await api.deletePerson(slug);
      toast.success(res.message);
      setConfirmDelete(null);
      setSelected(null);
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to forget person.");
    }
  };

  if (loading) return <Spinner />;

  return (
    <Card
      title="People & things"
      desc="Profiles the agent has learned about the people, projects, and places you mention."
    >
      {people.length === 0 ? (
        <Empty>No people learned yet. Mention someone in conversation and they'll appear here.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th style={{ width: 110 }}>Type</th>
                <th style={{ width: 140 }}>Relation</th>
                <th style={{ width: 70 }}>Facts</th>
                <th style={{ width: 90 }}></th>
              </tr>
            </thead>
            <tbody>
              {people.map((p) => (
                <tr key={p.slug}>
                  <td>
                    <button
                      className="btn btn-icon"
                      style={{ width: "auto", padding: "2px 4px", color: "var(--accent)" }}
                      onClick={() => openPerson(p.slug)}
                    >
                      {p.name}
                    </button>
                  </td>
                  <td>{p.type && <Badge kind="neutral">{p.type}</Badge>}</td>
                  <td>{p.relation}</td>
                  <td>
                    <Badge kind="accent">{p.facts}</Badge>
                  </td>
                  <td>
                    <div className="cell-actions">
                      <button
                        className="btn btn-icon danger"
                        title="Forget"
                        onClick={() => setConfirmDelete(p)}
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

      {selected && (
        <Modal
          open
          title={selected.name}
          desc={[selected.type, selected.relation].filter(Boolean).join(" · ") || undefined}
          onClose={() => setSelected(null)}
        >
          {selected.card ? (
            <div className="mono" style={{ whiteSpace: "pre-wrap", marginBottom: 16, fontSize: 13 }}>
              {selected.card}
            </div>
          ) : (
            <Empty>No profile card yet — not enough facts to summarize.</Empty>
          )}
          {selected.facts.length > 0 && (
            <Field label="Stored facts">
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {selected.facts.map((f) => (
                  <li key={f.id}>{f.content}</li>
                ))}
              </ul>
            </Field>
          )}
          <div className="modal-actions">
            <button className="btn btn-secondary" onClick={() => setSelected(null)}>
              Close
            </button>
          </div>
        </Modal>
      )}

      <Modal
        open={confirmDelete !== null}
        title={`Forget ${confirmDelete?.name}?`}
        desc="This permanently removes every fact and the profile card for this person."
        onClose={() => setConfirmDelete(null)}
      >
        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={() => setConfirmDelete(null)}>
            Cancel
          </button>
          <button className="btn btn-danger" onClick={() => confirmDelete && remove(confirmDelete.slug)}>
            <Trash2 size={16} /> Forget
          </button>
        </div>
      </Modal>
    </Card>
  );
}

function EventsPanel() {
  const toast = useToast();
  const [events, setEvents] = useState<MemoryEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [confirmWipe, setConfirmWipe] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setEvents(await api.listEvents());
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to load events.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const remove = async (id: string) => {
    try {
      await api.deleteEvent(id);
      toast.success("Event deleted.");
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Delete failed.");
    }
  };

  const wipe = async () => {
    try {
      const res = await api.wipeEvents();
      toast.success(res.message);
      setConfirmWipe(false);
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Wipe failed.");
    }
  };

  if (loading) return <Spinner />;

  return (
    <Card
      title="Events"
      desc="Time-anchored events the agent surfaces proactively as they approach."
    >
      <div className="row-between" style={{ marginBottom: 14, gap: 12 }}>
        <div />
        <div className="btn-row">
          <button className="btn btn-secondary btn-sm" onClick={() => setAdding(true)}>
            <Plus size={15} /> Add event
          </button>
          {events.length > 0 && (
            <button className="btn btn-danger btn-sm" onClick={() => setConfirmWipe(true)}>
              <Trash2 size={15} /> Wipe all
            </button>
          )}
        </div>
      </div>

      {events.length === 0 ? (
        <Empty>No events recorded yet.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: 130 }}>Date</th>
                <th>Title</th>
                <th style={{ width: 150 }}>Location</th>
                <th style={{ width: 100 }}>Status</th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {events.map((ev) => (
                <tr key={ev.id}>
                  <td>
                    {ev.date}
                    {ev.time_of_day ? ` ${ev.time_of_day}` : ""}
                    {ev.recurring && (
                      <Repeat size={13} color="var(--accent)" style={{ marginLeft: 6 }} />
                    )}
                  </td>
                  <td>{ev.title}</td>
                  <td>{ev.location}</td>
                  <td>
                    <Badge kind={ev.status === "upcoming" ? "accent" : "neutral"}>{ev.status}</Badge>
                  </td>
                  <td>
                    <div className="cell-actions">
                      <button
                        className="btn btn-icon danger"
                        title="Delete"
                        onClick={() => remove(ev.id)}
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

      {adding && (
        <EventModal
          onClose={() => setAdding(false)}
          onSaved={async () => {
            setAdding(false);
            await load();
          }}
        />
      )}

      <Modal
        open={confirmWipe}
        title="Wipe all events?"
        desc="This permanently deletes every stored event."
        onClose={() => setConfirmWipe(false)}
      >
        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={() => setConfirmWipe(false)}>
            Cancel
          </button>
          <button className="btn btn-danger" onClick={wipe}>
            <Trash2 size={16} /> Wipe everything
          </button>
        </div>
      </Modal>
    </Card>
  );
}

function EventModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const [timeOfDay, setTimeOfDay] = useState("");
  const [location, setLocation] = useState("");
  const [recurring, setRecurring] = useState(false);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!title.trim() || !date.trim()) return;
    setSaving(true);
    try {
      await api.addEvent({
        title,
        date,
        time_of_day: timeOfDay,
        location,
        recurring,
      });
      toast.success("Event added.");
      onSaved();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open title="Add event" desc="Log a dated event for proactive recall." onClose={onClose}>
      <Field label="Title">
        <input
          className="input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Jenna's birthday party"
        />
      </Field>
      <Field label="Date" hint="ISO format YYYY-MM-DD.">
        <input
          className="input"
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
      </Field>
      <Field label="Time of day" hint="Optional.">
        <input
          className="input"
          value={timeOfDay}
          onChange={(e) => setTimeOfDay(e.target.value)}
          placeholder="e.g. 19:00 or evening"
        />
      </Field>
      <Field label="Location" hint="Optional.">
        <input
          className="input"
          value={location}
          onChange={(e) => setLocation(e.target.value)}
        />
      </Field>
      <Field label="">
        <label className="switch">
          <input
            type="checkbox"
            checked={recurring}
            onChange={(e) => setRecurring(e.target.checked)}
          />
          Recurring annually (birthday, anniversary)
        </label>
      </Field>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>
          Cancel
        </button>
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={saving || !title.trim() || !date.trim()}
        >
          Save
        </button>
      </div>
    </Modal>
  );
}
