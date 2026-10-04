import { mdiHelpCircleOutline } from "@mdi/js";
import { CSSProperties, ReactNode, useRef, useState } from "react";
import Icon from "./Icon";

const WIDTH = 300;
const GAP = 8;

/** Иконка «?» с пояснением по наведению или фокусу (вместо длинных подписей под полями).
 * Окно позиционируется в пределах экрана: над иконкой, а если сверху мало места — под ней. */
export default function Hint({ children, size = 15 }: { children: ReactNode; size?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<CSSProperties | null>(null);

  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const width = Math.min(WIDTH, window.innerWidth - 2 * GAP);
    const left = Math.min(Math.max(GAP, r.left + r.width / 2 - width / 2), window.innerWidth - width - GAP);
    const above = r.top > 140;
    setPos(above
      ? { left, width, bottom: window.innerHeight - r.top + GAP }
      : { left, width, top: r.bottom + GAP });
  };

  return (
    <span ref={ref} className="hint" tabIndex={0} role="note"
      onMouseEnter={show} onFocus={show} onMouseLeave={() => setPos(null)} onBlur={() => setPos(null)}
      onClick={(e) => e.preventDefault()}>
      <Icon path={mdiHelpCircleOutline} size={size} />
      {pos && <span className="hint-bubble" style={pos}>{children}</span>}
    </span>
  );
}
