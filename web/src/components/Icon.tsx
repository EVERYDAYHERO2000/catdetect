/** Иконка Material Design (пути из @mdi/js). */
export default function Icon({ path, size = 20, className, title }: { path: string; size?: number; className?: string; title?: string }) {
  return (
    <svg className={`icon ${className ?? ""}`} width={size} height={size} viewBox="0 0 24 24" aria-hidden={title ? undefined : true} role={title ? "img" : undefined}>
      {title && <title>{title}</title>}
      <path d={path} fill="currentColor" />
    </svg>
  );
}
