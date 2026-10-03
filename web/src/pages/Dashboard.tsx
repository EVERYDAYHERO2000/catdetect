import { useState } from "react";
import { Link } from "react-router-dom";
import { CameraState, EventItem, FullState, IdentityState, SPECIES_LABEL, describeEvent, eventImage, fmtTime } from "../api";
import { mdiBrain, mdiCctvOff, mdiDoorOpen, mdiLanConnect, mdiLanDisconnect, mdiMotionSensor, mdiVideoOutline } from "@mdi/js";
import Icon from "../components/Icon";
import SpeciesIcon from "../components/SpeciesIcon";
import { useApi, useLive } from "../hooks";

export default function Dashboard() {
  const { data: state, setData: setState, reload, error } = useApi<FullState>("/api/state");
  const { data: initialEvents } = useApi<EventItem[]>("/api/events?limit=15");
  const [liveEvents, setLiveEvents] = useState<EventItem[]>([]);
  const [snapVer, setSnapVer] = useState<Record<number, number>>({});

  const connected = useLive((m) => {
    if (m.type === "camera_state") {
      setState((s) => s && { ...s, cameras: s.cameras.map((c) => (c.id === m.camera_id ? { ...c, state: m.state as CameraState } : c)) });
    } else if (m.type === "identity_state") {
      setState((s) => s && { ...s, identities: s.identities.map((i) => (i.id === m.identity_id ? { ...i, state: m.state as IdentityState } : i)) });
    } else if (m.type === "event") {
      setLiveEvents((l) => [m.event as EventItem, ...l].slice(0, 30));
    } else if (m.type === "snapshot") {
      setSnapVer((v) => ({ ...v, [m.camera_id as number]: Date.now() }));
    } else if (m.type === "config_changed") {
      reload();
    }
  });

  if (error) return <div className="error">{error}</div>;
  if (!state) return <div className="muted">Загрузка…</div>;

  const det = state.service.detector as { status?: string; weights?: string; error?: string; avg_ms?: number; compute?: { label: string } };
  const camName = (id: number) => state.cameras.find((c) => c.id === id)?.name ?? `#${id}`;
  const identName = (id: number | null) => (id ? state.identities.find((i) => i.id === id)?.name : null);
  const events = [...liveEvents, ...(initialEvents ?? []).filter((e) => !liveEvents.some((l) => l.id === e.id))].slice(0, 30);

  return (
    <div>
      <div className="row between mb">
        <h1 style={{ margin: 0 }}>Обзор</h1>
        <div className="row small">
          <span className={`badge ${det.status === "ready" ? "ok" : det.status === "error" ? "danger" : "warn"}`}>
            <Icon path={mdiBrain} size={14} />
            модель: {det.status === "ready" ? `${det.weights} · ${det.compute?.label ?? ""}${det.avg_ms ? ` · ${Math.round(det.avg_ms)} мс/кадр` : ""}` : det.status === "loading" ? "загружается…" : det.error ?? det.status}
          </span>
          <span className={`badge ${connected ? "ok" : "warn"}`}><Icon path={connected ? mdiLanConnect : mdiLanDisconnect} size={14} />{connected ? "онлайн" : "нет связи"}</span>
        </div>
      </div>

      {state.cameras.length === 0 && (
        <div className="notice mb">
          Камер пока нет. Добавьте <Link to="/nvrs">регистратор</Link>, затем <Link to="/cameras">камеры</Link>.
        </div>
      )}

      <div className="grid mb">
        {state.cameras.map((c) => {
          const st = c.state;
          return (
            <div className="panel stack" key={c.id}>
              <div className="row between">
                <Link to={`/cameras/${c.id}`}><h3 style={{ margin: 0 }}>{c.name}</h3></Link>
                <div className="row">
                  {!c.enabled && <span className="badge">выключена</span>}
                  {st?.motion && <span className="badge warn"><Icon path={mdiMotionSensor} size={14} />движение</span>}
                  <span className={`badge ${st?.online ? "ok" : "danger"}`}><Icon path={st?.online ? mdiVideoOutline : mdiCctvOff} size={14} />{st?.online ? "поток" : "нет потока"}</span>
                </div>
              </div>
              {st?.last_event ? (
                <img className="thumb" src={`/api/cameras/${c.id}/last_snapshot?v=${snapVer[c.id] ?? st.last_event.id}`} alt="" />
              ) : (
                <div className="thumb muted small" style={{ display: "flex", alignItems: "center", justifyContent: "center", background: "var(--bg)" }}>событий ещё не было</div>
              )}
              <div className="row">
                {c.species.map((sp) => (
                  <span key={sp} className={`badge ${st?.species[sp]?.present ? "on" : ""}`}>
                    <SpeciesIcon species={sp} size={14} />{SPECIES_LABEL[sp]}{st?.species[sp]?.at_door ? " · у двери" : ""}
                  </span>
                ))}
              </div>
              {st?.last_event && (
                <div className="small muted">
                  {fmtTime(st.last_event.ts)}: {describeEvent(st.last_event, identName(st.last_event.identity_id)).title}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="grid">
        <div className="panel">
          <h2>Объекты</h2>
          {state.identities.length === 0 ? (
            <div className="muted">Добавьте животных и людей на странице <Link to="/objects">«Объекты»</Link>.</div>
          ) : (
            <table>
              <tbody>
                {state.identities.map((i) => (
                  <tr key={i.id}>
                    <td><span className="title-row"><SpeciesIcon species={i.species} size={16} />{i.name}</span> {!i.is_own && <span className="badge small">чужой</span>}</td>
                    <td>
                      {i.state?.at_door ? <span className="badge on"><Icon path={mdiDoorOpen} size={14} />у двери</span> : i.state?.present ? <span className="badge ok">в кадре</span> : null}
                    </td>
                    <td className="small muted">
                      {i.state?.last_direction ? `${i.state.last_direction === "arrived" ? "пришёл" : "ушёл"}, ${fmtTime(i.state.last_seen)}` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="panel">
          <div className="row between"><h2>Последние события</h2><Link to="/events" className="small">все</Link></div>
          <div className="feed">
            {events.length === 0 && <div className="muted">Пока пусто</div>}
            {events.map((e) => (
              <div className="ev" key={e.id}>
                {e.has_snapshot && <img src={eventImage(e)} alt="" />}
                <div>
                  <div>
                    {e.kind === "motion" ? <span className="badge warn small">Движение</span> : <b>{describeEvent(e, identName(e.identity_id)).title}</b>}
                  </div>
                  <div className="small muted">
                    {camName(e.camera_id)} · {fmtTime(e.ts)}{e.kind === "motion" ? "" : ` · ${Math.round(e.confidence * 100)}%`}
                  </div>
                  {e.kind === "motion" && <div className="small muted">{describeEvent(e).note}</div>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
