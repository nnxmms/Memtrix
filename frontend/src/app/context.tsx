import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, type Config, type StatusResponse } from "../api";

// ----------------------------------------------------------------- config

interface ConfigContextValue {
  config: Config | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  setConfig: (config: Config) => void;
}

const ConfigContext = createContext<ConfigContextValue | null>(null);

export function useConfig(): ConfigContextValue {
  const ctx = useContext(ConfigContext);
  if (!ctx) throw new Error("useConfig must be used within ConfigProvider");
  return ctx;
}

export function ConfigProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const cfg = await api.getConfig();
      setConfig(cfg);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load configuration");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <ConfigContext.Provider value={{ config, loading, error, reload, setConfig }}>
      {children}
    </ConfigContext.Provider>
  );
}

// ----------------------------------------------------------------- status

interface StatusContextValue {
  status: StatusResponse | null;
  refresh: () => Promise<void>;
}

const StatusContext = createContext<StatusContextValue | null>(null);

export function useStatus(): StatusContextValue {
  const ctx = useContext(StatusContext);
  if (!ctx) throw new Error("useStatus must be used within StatusProvider");
  return ctx;
}

export function StatusProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.status());
    } catch {
      setStatus((s) => (s ? { ...s, agent_alive: false } : s));
    }
  }, []);

  useEffect(() => {
    void refresh();
    timer.current = window.setInterval(() => void refresh(), 5000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [refresh]);

  return (
    <StatusContext.Provider value={{ status, refresh }}>
      {children}
    </StatusContext.Provider>
  );
}
