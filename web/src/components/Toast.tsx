import { mdiAlertCircleOutline, mdiCheckCircleOutline, mdiClose, mdiInformationOutline } from "@mdi/js";
import { ReactNode, createContext, useCallback, useContext, useRef, useState } from "react";
import Icon from "./Icon";

type Kind = "success" | "error" | "info";
interface Toast {
  id: number;
  kind: Kind;
  text: string;
}

interface ToastApi {
  success: (text: string) => void;
  error: (text: string) => void;
  info: (text: string) => void;
}

const Ctx = createContext<ToastApi>({ success: () => {}, error: () => {}, info: () => {} });

const ICONS: Record<Kind, string> = { success: mdiCheckCircleOutline, error: mdiAlertCircleOutline, info: mdiInformationOutline };

/** Микронотификации: короткие сообщения в углу экрана, исчезают сами. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const seq = useRef(0);

  const remove = useCallback((id: number) => setItems((l) => l.filter((t) => t.id !== id)), []);
  const push = useCallback((kind: Kind, text: string) => {
    const id = ++seq.current;
    setItems((l) => [...l.slice(-3), { id, kind, text }]);
    window.setTimeout(() => remove(id), kind === "error" ? 6000 : 2800);
  }, [remove]);

  const api = useRef<ToastApi>({
    success: (t) => push("success", t),
    error: (t) => push("error", t),
    info: (t) => push("info", t),
  });
  api.current = { success: (t) => push("success", t), error: (t) => push("error", t), info: (t) => push("info", t) };

  return (
    <Ctx.Provider value={api.current}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <Icon path={ICONS[t.kind]} size={18} />
            <span>{t.text}</span>
            <button className="ghost icon-only" onClick={() => remove(t.id)} aria-label="Закрыть"><Icon path={mdiClose} size={16} /></button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
