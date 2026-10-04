import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { mdiAccountCogOutline, mdiBrain, mdiContentCopy, mdiHomeAssistant, mdiKeyPlus, mdiKeyRemove, mdiLockReset } from "@mdi/js";
import Hint from "../components/Hint";
import Icon from "../components/Icon";
import ModelsPanel from "../components/ModelsPanel";
import { useToast } from "../components/Toast";
import { api, fmtTime } from "../api";
import { useApi, useInterval } from "../hooks";

interface Token {
  id: number;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
}

interface ComputeInfo {
  preference: "auto" | "cpu" | "gpu";
  available: { cpu: boolean; gpu: string | null };
  active: { kind: string; label: string } | null;
  detector: { status?: string; avg_ms?: number; error?: string };
}

function ComputePanel() {
  const { data, reload } = useApi<ComputeInfo>("/api/system/compute");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  useInterval(reload, busy || data?.detector.status === "loading" ? 2000 : 10000);

  const choose = async (preference: ComputeInfo["preference"]) => {
    setBusy(true);
    await api("/api/system/compute", { method: "PUT", body: { preference } });
    toast.success(`Вычисления: ${{ auto: "автоматически", cpu: "процессор", gpu: "видеокарта" }[preference]}. Модель перезагружается…`);
    await reload();
    window.setTimeout(() => setBusy(false), 6000);
  };

  if (!data) return null;
  const gpu = data.available.gpu;
  const options: [ComputeInfo["preference"], string, string][] = [
    ["auto", "Автоматически", gpu ? `Сейчас будет использована видеокарта: ${gpu}.` : "Видеокарты нет — будет использован процессор."],
    ["cpu", "Процессор", "Через OpenVINO, видеокарта не нужна. Медленнее видеокарты, но обычно достаточно."],
    ["gpu", "Видеокарта", gpu ? `${gpu}. Быстрее всего.` : "Недоступна на этом компьютере."],
  ];
  return (
    <div className="panel stack" style={{ maxWidth: 560 }}>
      <h2 className="label-text">Вычисления<Hint>Чем считать распознавание и обучение моделей. Переключение занимает несколько секунд — модель перезагружается.</Hint></h2>
      <div className="stack" style={{ gap: 6 }}>
        {options.map(([value, title, hint]) => (
          <label key={value} className="check">
            <input type="radio" name="compute" checked={data.preference === value} disabled={busy || (value === "gpu" && !gpu)}
              onChange={() => choose(value)} />
            <b>{title}</b><Hint>{hint}</Hint>
          </label>
        ))}
      </div>
      <div className="small">
        Сейчас: {data.detector.status === "loading" ? "модель загружается…" : data.detector.status === "error" ? <span className="error">{data.detector.error}</span>
          : <>{data.active?.label ?? "—"}{data.detector.avg_ms ? ` · ${Math.round(data.detector.avg_ms)} мс на кадр` : ""}</>}
      </div>
    </div>
  );
}

export default function Settings() {
  const [qs, setQs] = useSearchParams();
  const { data: tokens, reload } = useApi<Token[]>("/api/auth/tokens");
  const [name, setName] = useState("Home Assistant");
  const [created, setCreated] = useState<string | null>(null);
  const [pw, setPw] = useState({ old_password: "", new_password: "" });
  const toast = useToast();

  const create = async () => {
    const r = await api<{ token: string }>("/api/auth/tokens", { body: { name } });
    setCreated(r.token);
    toast.success(`Токен «${name}» создан — скопируйте его`);
    reload();
  };
  const revoke = async (t: Token) => {
    if (!confirm(`Отозвать токен «${t.name}»? Интеграция, которая его использует, перестанет работать.`)) return;
    await api(`/api/auth/tokens/${t.id}`, { method: "DELETE" });
    toast.success(`Токен «${t.name}» отозван`);
    reload();
  };
  const changePw = async () => {
    try {
      await api("/api/auth/password", { body: pw });
      setPw({ old_password: "", new_password: "" });
      toast.success("Пароль изменён");
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const tab = qs.get("tab") ?? "models";
  const TABS = [
    ["models", "Распознавание и модели", mdiBrain],
    ["ha", "Home Assistant", mdiHomeAssistant],
    ["account", "Учётная запись", mdiAccountCogOutline],
  ] as const;

  return (
    <div className="stack">
      <h1 style={{ marginBottom: 0 }}>Настройки</h1>
      <div><div className="tabs">
        {TABS.map(([k, label, icon]) => (
          <button key={k} className={tab === k ? "active" : ""} onClick={() => setQs({ tab: k })}><Icon path={icon} size={18} />{label}</button>
        ))}
      </div></div>
      {tab === "models" && (
        <>
          <ComputePanel />
          <ModelsPanel />
        </>
      )}
      {tab === "ha" && (
      <div className="panel stack">
        <h2>Подключение Home Assistant</h2>
        <ol className="small" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
          <li>Установите интеграцию <b>CatDetect</b> в HA: через HACS (пользовательский репозиторий) или скопируйте папку <span className="mono">custom_components/catdetect</span> в конфигурацию HA.</li>
          <li>Создайте токен ниже и скопируйте его.</li>
          <li>В HA: <i>Настройки → Устройства и службы → Добавить интеграцию → CatDetect</i>. Укажите адрес <span className="mono">{location.origin}</span> и токен.</li>
        </ol>
        <div className="row">
          <input style={{ width: 240 }} value={name} onChange={(e) => setName(e.target.value)} />
          <button className="primary" onClick={create} disabled={!name}><Icon path={mdiKeyPlus} size={18} />Создать токен</button>
        </div>
        {created && (
          <div className="notice stack" style={{ gap: 6 }}>
            <div>Скопируйте токен сейчас: позже его не будет видно.</div>
            <div className="row">
              <input className="mono" readOnly value={created} onFocus={(e) => e.target.select()} />
              <button onClick={() => (navigator.clipboard?.writeText(created), toast.success("Токен скопирован"))}><Icon path={mdiContentCopy} size={18} />Копировать</button>
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
                <td style={{ textAlign: "right" }}><button className="small danger" onClick={() => revoke(t)}><Icon path={mdiKeyRemove} size={14} />Отозвать</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      )}
      {tab === "account" && (
      <div className="panel stack" style={{ maxWidth: 420 }}>
        <h2>Смена пароля</h2>
        <label className="field">Текущий пароль<input type="password" value={pw.old_password} onChange={(e) => setPw({ ...pw, old_password: e.target.value })} autoComplete="current-password" /></label>
        <label className="field">Новый пароль (≥ 8 символов)<input type="password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} autoComplete="new-password" /></label>
        <div><button onClick={changePw} disabled={!pw.old_password || pw.new_password.length < 8}><Icon path={mdiLockReset} size={18} />Изменить</button></div>
      </div>
      )}
    </div>
  );
}
