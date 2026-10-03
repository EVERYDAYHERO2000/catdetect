import { Link } from "react-router-dom";
import { Camera, Nvr, SPECIES_LABEL } from "../api";
import { useApi } from "../hooks";

export default function Cameras() {
  const { data: cams, error } = useApi<Camera[]>("/api/cameras");
  const { data: nvrs } = useApi<Nvr[]>("/api/nvrs");
  const nvrName = (id: number | null) => nvrs?.find((n) => n.id === id)?.name ?? "—";

  return (
    <div>
      <div className="row between mb">
        <h1 style={{ margin: 0 }}>Камеры</h1>
        <Link to="/cameras/new"><button className="primary">+ Добавить</button></Link>
      </div>
      {error && <div className="error mb">{error}</div>}
      {cams?.length === 0 && <div className="muted">Камер нет. Удобнее добавлять их со страницы регистратора после «Проверить связь».</div>}
      <div className="grid">
        {cams?.map((c) => (
          <Link to={`/cameras/${c.id}`} key={c.id} style={{ color: "inherit" }}>
            <div className="panel stack">
              <div className="row between">
                <h3 style={{ margin: 0 }}>{c.name}</h3>
                {!c.enabled && <span className="badge">выключена</span>}
              </div>
              <img className="thumb" src={`/api/cameras/${c.id}/snapshot?overlay=1`} alt="" loading="lazy" />
              <div className="small muted">
                <span className="mono">{c.slug}</span> · {c.source_url ? "свой источник" : `${nvrName(c.nvr_id)}, канал ${c.channel}`} ·{" "}
                {c.trigger === "motion" ? "по движению" : "постоянно"}
              </div>
              <div className="row">
                {c.species.map((s) => <span key={s} className={`badge ${s}`}>{SPECIES_LABEL[s]}</span>)}
                {c.zone && <span className="badge">зона</span>}
                {c.direction && <span className="badge">направление</span>}
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
