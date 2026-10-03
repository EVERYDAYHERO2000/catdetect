import { useState } from "react";
import { api, fmtTime } from "../api";
import { useApi } from "../hooks";

interface Token {
  id: number;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
}

export default function Settings() {
  const { data: tokens, reload } = useApi<Token[]>("/api/auth/tokens");
  const [name, setName] = useState("Home Assistant");
  const [created, setCreated] = useState<string | null>(null);
  const [pw, setPw] = useState({ old_password: "", new_password: "" });
  const [pwMsg, setPwMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const create = async () => {
    const r = await api<{ token: string }>("/api/auth/tokens", { body: { name } });
    setCreated(r.token);
    reload();
  };
  const revoke = async (t: Token) => {
    if (!confirm(`Отозвать токен «${t.name}»? Интеграция, которая его использует, перестанет работать.`)) return;
    await api(`/api/auth/tokens/${t.id}`, { method: "DELETE" });
    reload();
  };
  const changePw = async () => {
    try {
      await api("/api/auth/password", { body: pw });
      setPw({ old_password: "", new_password: "" });
      setPwMsg({ ok: true, text: "Пароль изменён" });
    } catch (e) {
      setPwMsg({ ok: false, text: (e as Error).message });
    }
  };

  return (
    <div className="stack">
      <h1>Настройки</h1>
      <div className="panel stack">
        <h2>Подключение Home Assistant</h2>
        <ol className="small" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
          <li>Установите интеграцию <b>CatDetect</b> в HA: через HACS (пользовательский репозиторий) или скопируйте папку <span className="mono">custom_components/catdetect</span> в конфигурацию HA.</li>
          <li>Создайте токен ниже и скопируйте его.</li>
          <li>В HA: <i>Настройки → Устройства и службы → Добавить интеграцию → CatDetect</i>. Укажите адрес <span className="mono">{location.origin}</span> и токен.</li>
        </ol>
        <div className="row">
          <input style={{ width: 240 }} value={name} onChange={(e) => setName(e.target.value)} />
          <button className="primary" onClick={create} disabled={!name}>Создать токен</button>
        </div>
        {created && (
          <div className="notice stack" style={{ gap: 6 }}>
            <div>Скопируйте токен сейчас: позже его не будет видно.</div>
            <div className="row">
              <input className="mono" readOnly value={created} onFocus={(e) => e.target.select()} />
              <button onClick={() => navigator.clipboard?.writeText(created)}>Копировать</button>
            </div>
          </div>
        )}
        <table>
          <thead><tr><th>Название</th><th>Токен</th><th>Создан</th><th>Использован</th><th /></tr></thead>
          <tbody>
            {tokens?.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
                <td className="mono small">{t.prefix}…</td>
                <td className="small">{fmtTime(t.created_at)}</td>
                <td className="small">{fmtTime(t.last_used_at)}</td>
                <td style={{ textAlign: "right" }}><button className="small danger" onClick={() => revoke(t)}>отозвать</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel stack" style={{ maxWidth: 420 }}>
        <h2>Смена пароля</h2>
        <label className="field">Текущий пароль<input type="password" value={pw.old_password} onChange={(e) => setPw({ ...pw, old_password: e.target.value })} autoComplete="current-password" /></label>
        <label className="field">Новый пароль (≥ 8 символов)<input type="password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} autoComplete="new-password" /></label>
        {pwMsg && <div className={pwMsg.ok ? "notice" : "error"}>{pwMsg.text}</div>}
        <div><button onClick={changePw} disabled={!pw.old_password || pw.new_password.length < 8}>Изменить</button></div>
      </div>
    </div>
  );
}
