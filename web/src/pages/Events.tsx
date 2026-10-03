import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Camera, EventItem, Identity, describeEvent, eventImage, api, fmtTime } from "../api";
import { mdiFolderArrowRightOutline, mdiFolderOutline } from "@mdi/js";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useToast } from "../components/Toast";
import { useApi } from "../hooks";

const TYPES = [
  ["", "Все события"],
  ["detections", "Все детекции"],
  ["cat", "Кошка"],
  ["dog", "Собака"],
  ["person", "Человек"],
  ["motion", "Движение"],
] as const;

function typeQuery(t: string): string {
  if (!t) return "";
  if (t === "detections" || t === "motion") return `&kind=${t}`;
  return `&species=${t}&kind=detections`;
}

export default function Events() {
  const { data: cams } = useApi<Camera[]>("/api/cameras");
  const { data: identities } = useApi<Identity[]>("/api/identities");
  const [camera, setCamera] = useState("");
  const [type, setType] = useState("");
  const [identity, setIdentity] = useState("");
  const [items, setItems] = useState<EventItem[]>([]);
  const [more, setMore] = useState(true);
  const [open, setOpen] = useState<EventItem | null>(null);
  const navigate = useNavigate();
  const toast = useToast();

  const toLabeling = async (e: EventItem) => {
    try {
      await api<{ image_id: number }>(`/api/events/${e.id}/label`, { method: "POST" });
      if (!e.image_id) toast.success("Кадр события добавлен в «Неразобранные»");
      navigate("/objects?folder=pending");
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const query = (before?: number) =>
    `/api/events?limit=50${camera ? `&camera_id=${camera}` : ""}${typeQuery(type)}${identity ? `&identity_id=${identity}` : ""}${before ? `&before_id=${before}` : ""}`;

  useEffect(() => {
    api<EventItem[]>(query()).then((r) => {
      setItems(r);
      setMore(r.length === 50);
    });
  }, [camera, type, identity]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadMore = async () => {
    const r = await api<EventItem[]>(query(items[items.length - 1]?.id));
    setItems([...items, ...r]);
    setMore(r.length === 50);
  };

  const camName = (id: number) => cams?.find((c) => c.id === id)?.name ?? `#${id}`;
  const identName = (id: number | null) => (id ? identities?.find((i) => i.id === id)?.name : null);

  return (
    <div>
      <h1>События</h1>
      <div className="row mb">
        <select style={{ width: 200 }} value={camera} onChange={(e) => setCamera(e.target.value)}>
          <option value="">Все камеры</option>
          {cams?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select style={{ width: 200 }} value={type} onChange={(e) => setType(e.target.value)}>
          {TYPES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
        <select style={{ width: 200 }} value={identity} onChange={(e) => setIdentity(e.target.value)}>
          <option value="">Все объекты</option>
          {identities?.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
        </select>
      </div>
      <div className="panel">
        <table>
          <thead><tr><th /><th>Время</th><th>Камера</th><th>Событие</th><th>Уверенность</th><th /></tr></thead>
          <tbody>
            {items.map((e) => {
              const d = describeEvent(e, identName(e.identity_id));
              return (
                <tr key={e.id} onClick={() => e.has_snapshot && setOpen(e)} style={{ cursor: e.has_snapshot ? "pointer" : undefined }}>
                  <td style={{ width: 120 }}>{e.has_snapshot && <img className="ev-thumb" src={eventImage(e)} loading="lazy" alt="" />}</td>
                  <td className="small">{fmtTime(e.ts)}</td>
                  <td>{camName(e.camera_id)}</td>
                  <td>
                    {e.kind === "motion" ? <span className="badge warn">{d.title}</span> : d.title}
                    {e.kind !== "motion" && !e.identity_id && <span className="muted small"> (не узнан)</span>}
                    {d.note && <div className="small muted">{d.note}</div>}
                  </td>
                  <td className="small">
                    {e.kind === "motion" ? "" : `${Math.round(e.confidence * 100)}%`}
                    {e.identity_confidence !== null && ` · узнан ${Math.round(e.identity_confidence * 100)}%`}
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {e.has_raw && (
                      <button className="small" title="Добавить найденных на кадре в «Неразобранные» раздела «Объекты»"
                        onClick={(ev) => (ev.stopPropagation(), toLabeling(e))}>
                        <Icon path={e.image_id ? mdiFolderOutline : mdiFolderArrowRightOutline} size={14} />{e.image_id ? "В объектах" : "В объекты"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {items.length === 0 && <div className="muted">Событий нет.</div>}
        {more && items.length > 0 && <button style={{ marginTop: 12 }} onClick={loadMore}>Загрузить ещё</button>}
      </div>
      {open && (
        <Modal title={`${camName(open.camera_id)} · ${fmtTime(open.ts)} · ${describeEvent(open, identName(open.identity_id)).title}`} onClose={() => setOpen(null)} wide>
          <img className="full-image" src={eventImage(open)} alt="" />
          {describeEvent(open).note && <div className="small muted" style={{ marginTop: 8 }}>{describeEvent(open).note}</div>}
          <div className="row" style={{ marginTop: 12 }}>
            {open.has_raw ? (
              <button className="primary" onClick={() => toLabeling(open)}>
                <Icon path={open.image_id ? mdiFolderOutline : mdiFolderArrowRightOutline} size={18} />{open.image_id ? "Открыть в объектах" : "Добавить в объекты"}
              </button>
            ) : (
              <span className="small muted">Для этого события нет исходного кадра (оно записано до появления функции).</span>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
