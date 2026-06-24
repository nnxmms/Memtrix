import { useState } from "react";
import { NavLink } from "react-router-dom";
import {
  Activity,
  Bot,
  Boxes,
  Brain,
  Cpu,
  KeyRound,
  Mail,
  MessageSquare,
  Mic,
  RotateCw,
  Server,
  Settings2,
  Sparkles,
  UserCog,
} from "lucide-react";
import { useStatus } from "./context";
import { Badge } from "../components/ui";
import { RestartModal } from "./RestartModal";

const NAV = [
  {
    label: "Overview",
    items: [{ to: "/", icon: Activity, text: "Dashboard", end: true }],
  },
  {
    label: "Configuration",
    items: [
      { to: "/main-agent", icon: Bot, text: "Main Agent" },
      { to: "/providers", icon: Server, text: "Providers" },
      { to: "/models", icon: Cpu, text: "Models" },
      { to: "/channels", icon: MessageSquare, text: "Channels" },
      { to: "/voice", icon: Mic, text: "Voice" },
      { to: "/email", icon: Mail, text: "Email" },
      { to: "/agents", icon: Boxes, text: "Sub-Agents" },
      { to: "/memory", icon: Brain, text: "Memory" },
      { to: "/secrets", icon: KeyRound, text: "Secrets" },
    ],
  },
  {
    label: "Administration",
    items: [{ to: "/memory-admin", icon: UserCog, text: "Memory Admin" }],
  },
];

export function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <span className="mark">
          <Sparkles size={17} />
        </span>
        Memtrix
      </div>
      {NAV.map((group) => (
        <div className="nav-group" key={group.label}>
          <div className="nav-group-label">{group.label}</div>
          {group.items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={(item as { end?: boolean }).end}
              className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
            >
              <item.icon />
              {item.text}
            </NavLink>
          ))}
        </div>
      ))}
      <div className="nav-group" style={{ marginTop: "auto" }}>
        <NavLink
          to="/settings"
          className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
        >
          <Settings2 />
          Panel Settings
        </NavLink>
      </div>
    </aside>
  );
}

export function Topbar() {
  const { status } = useStatus();
  const [restartOpen, setRestartOpen] = useState(false);
  const alive = status?.agent_alive ?? false;

  return (
    <div className="topbar">
      <div className="topbar-status">
        {status ? (
          alive ? (
            <Badge kind="success">
              <span className="dot dot-success" /> Agent online
            </Badge>
          ) : (
            <Badge kind="danger">
              <span className="dot dot-danger" /> Agent offline
            </Badge>
          )
        ) : (
          <Badge kind="neutral">Connecting…</Badge>
        )}
        {status?.deriver_paused && <Badge kind="warning">Reasoning paused</Badge>}
        {status && <span className="muted mono">v{status.version}</span>}
      </div>
      <button className="btn btn-primary btn-sm" onClick={() => setRestartOpen(true)}>
        <RotateCw size={15} /> Apply &amp; Restart
      </button>
      <RestartModal open={restartOpen} onClose={() => setRestartOpen(false)} />
    </div>
  );
}
