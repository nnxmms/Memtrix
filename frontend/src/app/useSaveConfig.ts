import { useState } from "react";
import { api, ApiError, type Config } from "../api";
import { useToast } from "../components/Toast";
import { useConfig } from "./context";

// Shared helper for config pages: validates and persists a full config document,
// surfacing per-section validation errors and a success toast.
export function useSaveConfig() {
  const { setConfig } = useConfig();
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  const save = async (next: Config): Promise<boolean> => {
    setSaving(true);
    setErrors([]);
    try {
      await api.putConfig(next);
      setConfig(next);
      toast.success("Saved. Use Apply & Restart to take effect.");
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.errors.length) {
        setErrors(e.errors);
        toast.error("Configuration is invalid — fix the errors and retry.");
      } else {
        toast.error(e instanceof Error ? e.message : "Save failed.");
      }
      return false;
    } finally {
      setSaving(false);
    }
  };

  return { save, saving, errors, setErrors };
}
