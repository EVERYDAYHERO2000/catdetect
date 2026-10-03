import { PointerEvent as RPointerEvent, ReactNode, useEffect, useRef, useState } from "react";
import type { Point } from "../api";

export interface CanvasGeom {
  W: number;
  H: number;
  /** нормализованные координаты → координаты SVG */
  x: (v: number) => number;
  y: (v: number) => number;
  /** экранные пиксели → единицы SVG (для толщины линий, радиусов и шрифтов) */
  px: (n: number) => number;
}

interface Props {
  src: string;
  children?: (g: CanvasGeom) => ReactNode;
  onDown?: (p: Point, e: RPointerEvent<SVGSVGElement>) => void;
  onMove?: (p: Point, e: RPointerEvent<SVGSVGElement>) => void;
  onUp?: (p: Point, e: RPointerEvent<SVGSVGElement>) => void;
  cursor?: string;
  onLoad?: () => void;
  onError?: () => void;
}

const clamp = (v: number) => Math.min(1, Math.max(0, v));

/** Изображение с SVG-слоем поверх; все координаты в событиях — нормализованные 0..1. */
export default function ImageCanvas({ src, children, onDown, onMove, onUp, cursor, onLoad, onError }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [ratio, setRatio] = useState(9 / 16);
  const [shown, setShown] = useState(1000);
  const W = 1000;
  const H = W * ratio;
  const geom: CanvasGeom = { W, H, x: (v) => v * W, y: (v) => v * H, px: (n) => (n * W) / shown };

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setShown(el.getBoundingClientRect().width || 1000));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const norm = (e: RPointerEvent<SVGSVGElement>): Point => {
    const r = svgRef.current!.getBoundingClientRect();
    return [clamp((e.clientX - r.left) / r.width), clamp((e.clientY - r.top) / r.height)];
  };

  return (
    <div className="canvas-wrap">
      <img
        src={src}
        draggable={false}
        onLoad={(e) => {
          const img = e.currentTarget;
          if (img.naturalWidth) setRatio(img.naturalHeight / img.naturalWidth);
          onLoad?.();
        }}
        onError={onError}
        alt=""
      />
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ cursor: cursor ?? "default", touchAction: "none" }}
        onPointerDown={(e) => {
          if (!onDown) return;
          (e.target as Element).setPointerCapture?.(e.pointerId);
          onDown(norm(e), e);
        }}
        onPointerMove={(e) => onMove?.(norm(e), e)}
        onPointerUp={(e) => onUp?.(norm(e), e)}
      >
        {children?.(geom)}
      </svg>
    </div>
  );
}
