import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, RotateCw, XCircle } from "lucide-react";
import { api, streamRestart, ApiError } from "../api";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { useStatus } from "./context";

type Phase = "idle" | "requesting" | "stopping" | "starting" | "ready" | "timeout" | "error";

export function RestartModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast();
  const { refresh } = useStatus();
  const [phase, setPhase] = useState<Phase>("idle");
  const [detail, setDetail] = useState("");
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    if (!open) {
      setPhase("idle");
      setDetail("");
      setErrors([]);
    }
  }, [open]);

  const begin = async () => {
    setErrors([]);
    setPhase("requesting");
    setDetail("Validating configuration...");
    try {
      await api.restart();
    } catch (e) {
      if (e instanceof ApiError && e.errors.length) {
        setErrors(e.errors);
      }
      setPhase("error");
      setDetail("Restart blocked — configuration is invalid.");
      return;
    }
    setPhase("stopping");
    setDetail("Restart requested...");
    streamRestart(
      (p, d) => {
        setPhase(p as Phase);
        setDetail(d);
      },
      () => {
        void refresh();
      }
    );
  };

  const busy = phase === "requesting" || phase === "stopping" || phase === "starting";

  return (
    <Modal
      open={open}
      title="Apply & Restart"
      desc="Validates the saved configuration and restarts the agent process to apply changes. The control panel stays online."
      onClose={busy ? () => undefined : onClose}
    >
      {phase === "idle" && (
        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={begin}>
            <RotateCw size={16} /> Restart agent
          </button>
        </div>
      )}

      {phase !== "idle" && (
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "14px 16px",
              background: "var(--neutral-bg)",
              borderRadius: 10,
            }}
          >
            {busy && <Loader2 className="spin" size={18} />}
            {phase === "ready" && <CheckCircle2 size={18} color="#16a34a" />}
            {(phase === "error" || phase === "timeout") && (
              <XCircle size={18} color="#dc2626" />
            )}
            <span>{detail}</span>
          </div>

          {errors.length > 0 && (
            <ul style={{ marginTop: 14, color: "var(--danger-text)", fontSize: 13 }}>
              {errors.map((err, i) => (
                <li key={i}>{err}</li>
              ))}
            </ul>
          )}

          <div className="modal-actions">
            {(phase === "ready" || phase === "error" || phase === "timeout") && (
              <button
                className="btn btn-primary"
                onClick={() => {
                  if (phase === "ready") toast.success("Agent restarted.");
                  onClose();
                }}
              >
                Done
              </button>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
