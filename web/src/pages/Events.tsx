import { useEffect, useState } from "react";
import { Camera, EVENT_LABEL, EventItem, Identity, SPECIES_LABEL, api, fmtTime } from "../api";
import Modal from "../components/Modal";
import { useApi } from "../hooks";

export default function Events() {
  const { data: cams } = useApi<Camera[]>("/api/cameras");
  const { data: identities } = useApi<Identity[]>("/api/identities");
  const [camera, setCamera] = useState("");
  const [identity, setIdentity] = useState("");
  const [items, setItems] = useState<EventItem[]>([]);
  const [more, setMore] = useState(true);
  const [open, setOpen] = useState<EventItem | null>(null);

  const query = (before?: number) =>
    `/api/events?limit=50${camera ? `&camera_id=${camera}` : ""}${identity ? `&identity_id=${identity}` : ""}${before ? `&before_id=${before}` : ""}`;

  useEffect(() => {
    api<EventItem[]>(query()).then((r) => {
      setItems(r);
      setMore(r.length === 50);
    });
  }, [camera, identity]); // eslint-disable-line react-hooks/exhaustive-deps

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
        <select style={{ width: 200 }} value={identity} onChange={(e) => setIdentity(e.target.value)}>
          <option value="">Все животные</option>
          {identities?.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
        </select>
      </div>
      <div className="panel">
        <table>
          <thead><tr><th /><th>Время</th><th>Камера</th><th>Кто</th><th>Событие</th><th>Уверенность</th></tr></thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.id} onClick={() => e.has_snapshot && setOpen(e)} style={{ cursor: e.has_snapshot ? "pointer" : undefined }}>
                <td style={{ width: 110 }}>{e.has_snapshot && <img src={`/api/events/${e.id}/image`} style={{ width: 96, height: 54, objectFit: "cover", borderRadius: 4 }} loading="lazy" alt="" />}</td>
                <td className="small">{fmtTime(e.ts)}</td>
                <td>{camName(e.camera_id)}</td>
                <td>
                  {identName(e.identity_id) ?? <span className="muted">{SPECIES_LABEL[e.species]} (не узнан)</span>}
                </td>
                <td>{EVENT_LABEL[e.kind]}</td>
                <td className="small">
                  {Math.round(e.confidence * 100)}%{e.identity_confidence !== null && ` · узнан ${Math.round(e.identity_confidence * 100)}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && <div className="muted">Событий нет.</div>}
        {more && items.length > 0 && <button style={{ marginTop: 12 }} onClick={loadMore}>Загрузить ещё</button>}
      </div>
      {open && (
        <Modal title={`${camName(open.camera_id)} · ${fmtTime(open.ts)}`} onClose={() => setOpen(null)} wide>
          <img src={`/api/events/${open.id}/image`} style={{ width: "100%" }} alt="" />
        </Modal>
      )}
    </div>
  );
}
