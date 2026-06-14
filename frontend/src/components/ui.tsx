import type { ReactNode } from "react";

export function Card({
  title,
  desc,
  actions,
  children,
}: {
  title?: string;
  desc?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="card">
      {(title || actions) && (
        <div className="card-header">
          <div>
            {title && <h3 className="card-title">{title}</h3>}
            {desc && <p className="card-desc">{desc}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </div>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label className="field-label">{label}</label>
      {children}
      {hint && !error && <div className="field-hint">{hint}</div>}
      {error && <div className="field-error">{error}</div>}
    </div>
  );
}

export function Badge({
  kind,
  children,
}: {
  kind: "success" | "warning" | "danger" | "accent" | "neutral" | "attention";
  children: ReactNode;
}) {
  return <span className={`badge badge-${kind}`}>{children}</span>;
}

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="page-header">
      <h1 className="page-title">{title}</h1>
      {subtitle && <p className="page-subtitle">{subtitle}</p>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Spinner() {
  return <div className="spinner" />;
}
