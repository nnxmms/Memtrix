import { useEffect, useState } from "react";
import { Brain, Cpu, MessageSquare, Server } from "lucide-react";
import { api, type Config } from "../api";
import { Card, PageHeader, Badge, Spinner } from "../components/ui";
import { useConfig, useStatus } from "../app/context";

function countSection(config: Config | null, key: string): number {
  const section = config?.[key];
  return section && typeof section === "object" ? Object.keys(section).length : 0;
}

export function DashboardPage() {
  const { config, loading } = useConfig();
  const { status } = useStatus();
  const [memoryCount, setMemoryCount] = useState<number | null>(null);

  useEffect(() => {
    api
      .status()
      .then((s) => setMemoryCount(s.memory_count))
      .catch(() => setMemoryCount(null));
  }, []);

  const mainModel = config?.["main-agent"]?.model ?? "—";
  const mainChannel = config?.["main-agent"]?.channel ?? "—";

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="Live status of your Memtrix deployment and a snapshot of its configuration."
      />

      {loading ? (
        <Spinner />
      ) : (
        <>
          <div className="grid grid-3">
            <Card>
              <div className="metric">
                <span className="metric-label">Agent</span>
                <span className="metric-value">
                  {status?.agent_alive ? "Online" : "Offline"}
                </span>
                {status?.agent_alive ? (
                  <Badge kind="success">
                    <span className="dot dot-success" /> Heartbeat healthy
                  </Badge>
                ) : (
                  <Badge kind="danger">
                    <span className="dot dot-danger" /> No heartbeat
                  </Badge>
                )}
              </div>
            </Card>
            <Card>
              <div className="metric">
                <span className="metric-label">Reasoning memory</span>
                <span className="metric-value">{memoryCount ?? "—"}</span>
                <span className="muted" style={{ fontSize: 13 }}>
                  stored conclusions
                </span>
              </div>
            </Card>
            <Card>
              <div className="metric">
                <span className="metric-label">Background reasoning</span>
                <span className="metric-value">
                  {status?.deriver_paused ? "Paused" : "Active"}
                </span>
                {status?.deriver_paused ? (
                  <Badge kind="warning">Paused</Badge>
                ) : (
                  <Badge kind="accent">Running</Badge>
                )}
              </div>
            </Card>
          </div>

          <Card title="Configuration summary" desc="Resources currently configured.">
            <div className="grid grid-2">
              <SummaryRow icon={Server} label="Providers" value={countSection(config, "providers")} />
              <SummaryRow icon={Cpu} label="Models" value={countSection(config, "models")} />
              <SummaryRow icon={MessageSquare} label="Channels" value={countSection(config, "channels")} />
              <SummaryRow icon={Brain} label="Sub-agents" value={countSection(config, "agents")} />
            </div>
            <div style={{ marginTop: 18 }} className="muted">
              Main agent runs <span className="inline-code">{mainModel}</span> on{" "}
              <span className="inline-code">{mainChannel}</span>.
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function SummaryRow({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Server;
  label: string;
  value: number;
}) {
  return (
    <div className="row-between" style={{ padding: "6px 0" }}>
      <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon size={17} color="var(--text-muted)" />
        {label}
      </span>
      <Badge kind="neutral">{value}</Badge>
    </div>
  );
}
