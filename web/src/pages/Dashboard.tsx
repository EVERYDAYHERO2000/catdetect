import {
  mdiArrowRight, mdiBellOutline, mdiCctv, mdiChip, mdiFolderMultipleImage, mdiLanConnect, mdiLanDisconnect,
  mdiMotionSensor, mdiSchool, mdiServerNetwork,
} from "@mdi/js";
import { ReactNode, useRef } from "react";
import { Link } from "react-router-dom";
import { EventItem, FullState, Species, describeEvent } from "../api";
import Icon from "../components/Icon";
import SpeciesIcon from "../components/SpeciesIcon";
import { useApi, useInterval, useLive } from "../hooks";

interface Job { id: number; kind: "detector" | "classifier"; status: string; created_at: string; finished_at: string | null; progress: number; message: string }

interface Overview {
  cameras: { total: number; enabled: number; online: number; motion: number; items: { id: number; name: string; enabled: boolean; online?: boolean; motion?: boolean }[] };
  nvrs: { total: number; enabled: number; connected: number };
  objects: { folders: number; assigned: number; pending: number; rejected: number; top: { id: number; name: string; species: Species; count: number }[]; by_species: Record<Species, number> };
  events: { today: number; last_24h: number; motion_today: number; total: number; last: EventItem | null };
  training: { jobs: number; done: number; running: Job | null; last: Job | null; active: Partial<Record<"detector" | "classifier", { id: number; name: string; created_at: string }>>; new_since_last: number };
  system: {
    cpu_percent: number; cpu_count: number; load_avg: number[] | null; process_cpu_percent: number;
    mem_percent: number; mem_used: number; mem_total: number; process_rss: number;
    disk_percent: number; disk_used: number; disk_total: number; uptime: number; platform: string;
    compute: { label: string; kind: string } | null; detector_status: string; inference_ms: number | null; inference_fps: number | null;
    gpu: { name: string; mem_used: number | null; mem_total: number | null } | null;
  };
}

const KIND = { detector: "Поиск на кадре", classifier: "Узнавание" } as const;
const JOB_STATUS: Record<string, string> = { queued: "в очереди", running: "идёт", done: "готово", failed: "ошибка", cancelled: "отменено" };

const gb = (b: number) => (b >= 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(1)} ГБ` : `${Math.round(b / 1024 ** 2)} МБ`);

function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "только что";
  if (s < 3600) return `${Math.floor(s / 60)} мин назад`;
  if (s < 86400) return `${Math.floor(s / 3600)} ч назад`;
  return `${Math.floor(s / 86400)} дн назад`;
}

function uptime(sec: number): string {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d} дн ${h} ч` : h ? `${h} ч ${m} мин` : `${m} мин`;
}

function Teaser({ to, icon, title, value, unit, children }: { to: string; icon: string; title: string; value: ReactNode; unit?: string; children?: ReactNode }) {
  return (
    <Link to={to} className="panel teaser">
      <div className="teaser-head">
        <span className="teaser-icon"><Icon path={icon} /></span>
        <span className="teaser-title">{title}</span>
        <Icon path={mdiArrowRight} size={18} className="teaser-go" />
      </div>
      <div className="teaser-value">{value}{unit && <span className="teaser-unit">{unit}</span>}</div>
      <div className="teaser-body">{children}</div>
    </Link>
  );
}

function Meter({ label, percent, detail }: { label: string; percent: number; detail?: string }) {
  const level = percent >= 90 ? "danger" : percent >= 70 ? "warn" : "";
  return (
    <div className="meter">
      <div className="row between small"><span>{label}</span><span className="muted">{detail ?? `${Math.round(percent)}%`}</span></div>
      <div className={`progress ${level}`}><div style={{ width: `${Math.min(100, percent)}%` }} /></div>
    </div>
  );
}

/** «Обзор»: карточки-тизеры всех разделов. */
export default function Dashboard() {
  const { data: ov, reload } = useApi<Overview>("/api/overview");
  const { data: state, reload: reloadState } = useApi<FullState>("/api/state");
  const pending = useRef<number | undefined>(undefined);

  useInterval(reload, 5000); // нагрузка и счётчики
  const reloadSoon = () => {
    window.clearTimeout(pending.current);
    pending.current = window.setTimeout(reload, 800);
  };

  const connected = useLive((m) => {
    if (m.type === "event" || m.type === "camera_state") reloadSoon();
    else if (m.type === "config_changed") {
      reload();
      reloadState();
    }
  });

  if (!ov) return <div className="muted">Загрузка…</div>;
  const { cameras, nvrs, objects, events, training, system } = ov;
  const identName = (id: number | null) => (id ? state?.identities.find((i) => i.id === id)?.name : null);

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row between">
        <h1 style={{ margin: 0 }}>Обзор</h1>
        <span className={`badge ${connected ? "ok" : "warn"}`}><Icon path={connected ? mdiLanConnect : mdiLanDisconnect} size={14} />{connected ? "онлайн" : "нет связи"}</span>
      </div>

      <div className="teasers">
        <Teaser to="/cameras" icon={mdiCctv} title="Камеры" value={cameras.total}>
          <div className="row small">
            <span className="badge ok">поток {cameras.online}/{cameras.enabled}</span>
            {cameras.motion > 0 && <span className="badge warn"><Icon path={mdiMotionSensor} size={12} />движение {cameras.motion}</span>}
            {cameras.total - cameras.enabled > 0 && <span className="badge">выключено {cameras.total - cameras.enabled}</span>}
          </div>
          <div className="teaser-list">
            {cameras.items.slice(0, 4).map((c) => (
              <div key={c.id} className="row small">
                <span className={`dot ${!c.enabled ? "" : c.online ? "ok" : "danger"}`} />
                <span>{c.name}</span>
                {c.motion && <Icon path={mdiMotionSensor} size={14} className="warn-text" />}
              </div>
            ))}
            {cameras.total === 0 && <span className="small muted">Добавьте камеры с регистратора</span>}
          </div>
        </Teaser>

        <Teaser to="/nvrs" icon={mdiServerNetwork} title="Регистраторы" value={nvrs.total}>
          <div className="row small">
            <span className={`badge ${nvrs.connected === nvrs.enabled && nvrs.enabled ? "ok" : "warn"}`}>события: {nvrs.connected}/{nvrs.enabled}</span>
          </div>
          <div className="small muted">
            {nvrs.total === 0 ? "Добавьте регистратор Dahua" : nvrs.connected < nvrs.enabled ? "Не все регистраторы присылают события движения" : "Все подключены и присылают события движения"}
          </div>
        </Teaser>

        <Teaser to="/objects" icon={mdiFolderMultipleImage} title="Объекты" value={objects.folders} unit="папок">
          <div className="row small">
            <span className="badge">разложено {objects.assigned}</span>
            {objects.pending > 0 && <span className="badge warn">ждут разбора {objects.pending}</span>}
          </div>
          <div className="teaser-list">
            {objects.top.slice(0, 4).map((f) => (
              <div key={f.id} className="row small between">
                <span className="title-row"><SpeciesIcon species={f.species} size={14} />{f.name}</span>
                <span className="muted">{f.count}</span>
              </div>
            ))}
          </div>
        </Teaser>

        <Teaser to="/events" icon={mdiBellOutline} title="События" value={events.today} unit="сегодня">
          <div className="row small">
            <span className="badge">за сутки {events.last_24h}</span>
            <span className="badge">движений сегодня {events.motion_today}</span>
          </div>
          <div className="small muted">
            {events.last ? <>Последнее: {describeEvent(events.last, identName(events.last.identity_id)).title}, {ago(events.last.ts)}</> : "Событий пока не было"}
          </div>
        </Teaser>

        <Teaser to="/settings?tab=models" icon={mdiSchool} title="Обучение" value={training.done} unit={`из ${training.jobs}`}>
          {training.running ? (
            <div className="stack" style={{ gap: 4 }}>
              <div className="small">{KIND[training.running.kind]}: {training.running.message || "идёт"}</div>
              <div className="progress"><div style={{ width: `${training.running.progress * 100}%` }} /></div>
            </div>
          ) : (
            <div className="small muted">
              {training.last ? <>Последнее: {KIND[training.last.kind]}, {JOB_STATUS[training.last.status]}, {ago(training.last.finished_at ?? training.last.created_at)}</> : "Ещё не обучали"}
            </div>
          )}
          <div className="row small">
            <span className={`badge ${training.active.classifier ? "ok" : ""}`}>узнавание: {training.active.classifier ? "включено" : "выключено"}</span>
            <span className="badge">{training.active.detector ? "поиск: дообученный" : "поиск: стандартный"}</span>
          </div>
          {training.new_since_last >= 10 && <div className="small warn-text">Новых снимков с последнего обучения: {training.new_since_last}</div>}
        </Teaser>

        <Teaser to="/settings?tab=models" icon={mdiChip} title="Система" value={system.inference_ms ?? "—"} unit="мс/кадр">
          <div className="small muted">
            {system.compute?.label ?? "модель загружается"}
            {system.inference_fps ? ` · ${system.inference_fps} кадр/с` : " · ждёт движения"}
          </div>
          <Meter label="Процессор" percent={system.cpu_percent} detail={`${Math.round(system.cpu_percent)}% · сервис ${system.process_cpu_percent}%`} />
          <Meter label="Память" percent={system.mem_percent} detail={`${gb(system.mem_used)} из ${gb(system.mem_total)} · сервис ${gb(system.process_rss)}`} />
          <Meter label="Диск с данными" percent={system.disk_percent} detail={`${gb(system.disk_used)} из ${gb(system.disk_total)}`} />
          {system.gpu && system.gpu.mem_used !== null && (
            system.gpu.mem_total
              ? <Meter label="Видеопамять" percent={(system.gpu.mem_used / system.gpu.mem_total) * 100} detail={`${gb(system.gpu.mem_used)} из ${gb(system.gpu.mem_total)}`} />
              : <div className="small muted">Видеопамять: {gb(system.gpu.mem_used)}</div>
          )}
          <div className="small muted">Работает {uptime(system.uptime)} · {system.platform}</div>
        </Teaser>
      </div>
    </div>
  );
}
