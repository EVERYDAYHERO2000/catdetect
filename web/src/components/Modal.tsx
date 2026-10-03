import { mdiClose } from "@mdi/js";
import { ReactNode, useEffect } from "react";
import Icon from "./Icon";

export default function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={wide ? { maxWidth: 960 } : undefined}>
        <div className="row between mb">
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="ghost icon-only" onClick={onClose} aria-label="Закрыть"><Icon path={mdiClose} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}
