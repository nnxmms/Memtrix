import type { ReactNode } from "react";

export function Modal({
  open,
  title,
  desc,
  children,
  onClose,
}: {
  open: boolean;
  title: string;
  desc?: string;
  children: ReactNode;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" role="dialog" aria-modal="true">
        <h2 className="modal-title">{title}</h2>
        {desc && <p className="modal-desc">{desc}</p>}
        {children}
      </div>
    </div>
  );
}
