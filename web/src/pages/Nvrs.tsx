import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Nvr, api } from "../api";
import Modal from "../components/Modal";
import { useApi } from "../hooks";

interface ProbeResult {
  ok: boolean;
  error?: string;
  device_type?: string;
  serial?: string;
  channels?: { channel: number; name: string }[];
}

type Form = Omit<Nvr, "id" | "has_password" | "events_connected"> & { password: string };

const EMPTY: Form = { name: "", host: "", http_port: 80, rtsp_port: 554, https: false, username: "admin", password: "", event_codes: ["VideoMotion"], enabled: true };

export default function Nvrs() {
  const { data: nvrs, reload, error } = useApi<Nvr[]>("/api/nvrs");
  const [edit, setEdit] = useState<{ id: number | null; form: Form } | null>(null);
  const [probe, setProbe] = useState<Record<number, ProbeResult>>({});
  const [testing, setTesting] = useState<number | null>(null);
  const navigate = useNavigate();

  const test = async (id: number) => {
    setTesting(id);
    try {
      const r = await api<ProbeResult>(`/api/nvrs/${id}/test`, { method: "POST" });
      setProbe((p) => ({ ...p, [id]: r }));
    } catch (e) {
      setProbe((p) => ({ ...p, [id]: { ok: false, error: (e as Error).message } }));
    } finally {
      setTesting(null);
    }
  };

  const remove = async (n: Nvr) => {
    if (!confirm(`Удалить регистратор «${n.name}»?`)) return;
    try {
      await api(`/api/nvrs/${n.id}`, { method: "DELETE" });
      reload();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  return (
    <div>
      <div className="row between mb">
        <h1 style={{ margin: 0 }}>Регистраторы</h1>
        <button className="primary" onClick={() => setEdit({ id: null, form: { ...EMPTY } })}>+ Добавить</button>
      </div>
      {error && <div className="error mb">{error}</div>}
      <div className="stack">
        {nvrs?.length === 0 && <div className="muted">Регистраторов пока нет.</div>}
        {nvrs?.map((n) => {
          const p = probe[n.id];
          return (
            <div className="panel stack" key={n.id}>
              <div className="row between">
                <div>
                  <h3 style={{ margin: 0 }}>{n.name} {!n.enabled && <span className="badge">выключен</span>}</h3>
                  <div className="small muted mono">{n.https ? "https" : "http"}://{n.host}:{n.http_port} · rtsp :{n.rtsp_port} · {n.username}</div>
                </div>
                <div className="row">
                  {n.events_connected !== undefined && n.events_connected !== null && (
                    <span className={`badge ${n.events_connected ? "ok" : "danger"}`}>{n.events_connected ? "события: подключено" : "события: нет связи"}</span>
                  )}
                  <button onClick={() => test(n.id)} disabled={testing === n.id}>{testing === n.id ? "Проверка…" : "Проверить связь"}</button>
                  <button onClick={() => setEdit({ id: n.id, form: { ...n, password: "" } })}>Изменить</button>
                  <button className="danger" onClick={() => remove(n)}>Удалить</button>
                </div>
              </div>
              {p && (p.ok ? (
                <div className="stack">
                  <div className="notice">✓ Связь есть: {p.device_type} {p.serial && <span className="muted">· S/N {p.serial}</span>}</div>
                  {p.channels && p.channels.length > 0 && (
                    <table>
                      <thead><tr><th>Канал</th><th>Название</th><th /></tr></thead>
                      <tbody>
                        {p.channels.map((c) => (
                          <tr key={c.channel}>
                            <td>{c.channel}</td>
                            <td>{c.name}</td>
                            <td style={{ textAlign: "right" }}>
                              <button className="small" onClick={() => navigate(`/cameras/new?nvr=${n.id}&channel=${c.channel}&name=${encodeURIComponent(c.name)}`)}>
                                Добавить камеру
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              ) : (
                <div className="error">{p.error}</div>
              ))}
            </div>
          );
        })}
      </div>
      {edit && <NvrForm initial={edit} onClose={() => setEdit(null)} onSaved={() => (setEdit(null), reload())} />}
    </div>
  );
}

function NvrForm({ initial, onClose, onSaved }: { initial: { id: number | null; form: Form }; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<Form>(initial.form);
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }));

  const body = () => ({ ...f, password: f.password || (initial.id ? null : "") });

  const save = async () => {
    setBusy(true);
    try {
      await api(initial.id ? `/api/nvrs/${initial.id}` : "/api/nvrs", { method: initial.id ? "PUT" : "POST", body: body() });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true);
    setProbe(null);
    try {
      setProbe(initial.id && !f.password
        ? await api<ProbeResult>(`/api/nvrs/${initial.id}/test`, { method: "POST" })
        : await api<ProbeResult>("/api/nvrs/test", { body: body() }));
    } catch (e) {
      setProbe({ ok: false, error: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={initial.id ? "Регистратор" : "Новый регистратор"} onClose={onClose}>
      <div className="stack">
        <div className="form-grid">
          <label className="field">Название<input value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="Дом" /></label>
          <label className="field">Адрес (IP или имя)<input value={f.host} onChange={(e) => set("host", e.target.value)} placeholder="192.168.1.50" /></label>
          <label className="field">HTTP-порт<input type="number" value={f.http_port} onChange={(e) => set("http_port", +e.target.value)} /></label>
          <label className="field">RTSP-порт<input type="number" value={f.rtsp_port} onChange={(e) => set("rtsp_port", +e.target.value)} /></label>
          <label className="field">Логин<input value={f.username} onChange={(e) => set("username", e.target.value)} autoComplete="off" /></label>
          <label className="field">
            Пароль{initial.id ? " (пусто — не менять)" : ""}
            <input type="password" value={f.password} onChange={(e) => set("password", e.target.value)} autoComplete="new-password" />
          </label>
          <label className="field">
            Коды событий движения
            <input value={f.event_codes.join(",")} onChange={(e) => set("event_codes", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
          </label>
        </div>
        <div className="row">
          <label className="check"><input type="checkbox" checked={f.https} onChange={(e) => set("https", e.target.checked)} />HTTPS</label>
          <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} />Включён</label>
        </div>
        <div className="small muted">
          Рекомендуется завести на регистраторе отдельного пользователя с правами только на просмотр. Пароль хранится в базе сервиса вне Яндекс Диска.
        </div>
        {probe && (probe.ok ? <div className="notice">✓ {probe.device_type}, каналов: {probe.channels?.length ?? "?"}</div> : <div className="error">{probe.error}</div>)}
        {error && <div className="error">{error}</div>}
        <div className="row">
          <button className="primary" onClick={save} disabled={busy}>Сохранить</button>
          <button onClick={test} disabled={busy || !f.host}>Проверить связь</button>
        </div>
      </div>
    </Modal>
  );
}
