import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Camera, Nvr, Point, Prediction, SPECIES_LABEL, Species, api } from "../api";
import {
  mdiContentSaveOutline, mdiCursorDefault, mdiDeleteOutline, mdiDoorOpen, mdiEraser, mdiImageSearchOutline, mdiPlay,
  mdiRefresh, mdiStop, mdiVectorLine, mdiVectorPolygon,
} from "@mdi/js";
import ChannelGrid, { ChannelInfo } from "../components/ChannelGrid";
import Icon from "../components/Icon";
import { useToast } from "../components/Toast";
import ImageCanvas from "../components/ImageCanvas";
import { useApi } from "../hooks";

type Form = Omit<Camera, "id">;
type Mode = "none" | "zone" | "line" | "door";
type Handle = { kind: "zone"; i: number } | { kind: "line"; i: 0 | 1 } | { kind: "door" };

const DEFAULTS: Form = {
  slug: "", name: "", nvr_id: null, channel: 1, stream: "sub", source_url: null, trigger: "motion", fps: 5, linger: 10,
  clear_after: 10, species: ["cat", "dog"], zone: null, direction: null, min_conf: 0.25, confirm_hits: 3,
  confirm_conf: 0.5, identity_conf: 0.6, save_frames: true, enabled: true, aspect: null,
};

const HINT: Record<Mode, string> = {
  none: "Перетаскивайте точки мышью. Выберите инструмент, чтобы рисовать.",
  zone: "Кликайте, чтобы добавить вершины зоны. Всё, что вне зоны, игнорируется.",
  line: "Поставьте две точки линии, которую пересекают по пути к двери.",
  door: "Кликните на стороне линии, где находится дверь.",
};

const translit = (s: string) =>
  s.toLowerCase().replace(/[а-яё]/g, (c) => ({ а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "c", ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya" })[c] ?? "")
    .replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 32);

export default function CameraEdit() {
  const { id } = useParams();
  const isNew = id === "new";
  const [qs] = useSearchParams();
  const navigate = useNavigate();
  const { data: nvrs } = useApi<Nvr[]>("/api/nvrs");
  const [f, setF] = useState<Form | null>(null);
  const [slugTouched, setSlugTouched] = useState(!isNew);
  const [mode, setMode] = useState<Mode>("none");
  const [drag, setDrag] = useState<Handle | null>(null);
  const [snapVer, setSnapVer] = useState(Date.now());
  const [snapError, setSnapError] = useState(false);
  const [dets, setDets] = useState<Prediction[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [, setSaved] = useState(false);
  const [live, setLive] = useState(false);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const channelNames = useRef(new Set<string>()); // имена, подставленные из каналов (их можно заменять)

  useEffect(() => {
    if (isNew) {
      const name = qs.get("name") ?? "";
      channelNames.current.add(name);
      setF({ ...DEFAULTS, name, slug: translit(name), nvr_id: qs.get("nvr") ? +qs.get("nvr")! : null, channel: +(qs.get("channel") ?? 1) });
    } else {
      api<Camera>(`/api/cameras/${id}`).then(({ id: _omit, ...rest }) => setF(rest)).catch((e) => setError(e.message));
    }
  }, [id, isNew, qs]);

  useEffect(() => {
    if (isNew && f && !f.nvr_id && !f.source_url && nvrs?.length) setF({ ...f, nvr_id: nvrs[0].id });
  }, [nvrs]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!f) return error ? <div className="error">{error}</div> : <div className="muted">Загрузка…</div>;

  const set = <K extends keyof Form>(k: K, v: Form[K]) => {
    setSaved(false);
    setF((x) => x && { ...x, [k]: v });
  };
  const num = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) => set(k, +e.target.value as never);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = { ...f, direction: f.direction?.line && f.direction.door_point ? f.direction : null };
      const r = await api<Camera>(isNew ? "/api/cameras" : `/api/cameras/${id}`, { method: isNew ? "POST" : "PUT", body });
      setSaved(true);
      toast.success(isNew ? `Камера «${r.name}» добавлена` : "Настройки камеры сохранены");
      window.setTimeout(() => setSnapVer(Date.now()), 2500); // поток перезапускается с новыми настройками
      if (isNew) navigate(`/cameras/${r.id}`, { replace: true });
    } catch (e) {
      setError((e as Error).message);
      toast.error("Не удалось сохранить: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!confirm(`Удалить камеру «${f.name}»? История событий будет удалена, кадры для обучения сохранятся.`)) return;
    await api(`/api/cameras/${id}`, { method: "DELETE" });
    toast.success(`Камера «${f.name}» удалена`);
    navigate("/cameras");
  };

  const testDetect = async () => {
    setDets(null);
    try {
      const r = await api<{ detections: Prediction[] }>(`/api/cameras/${id}/detect`, { method: "POST" });
      setDets(r.detections);
      setSnapVer(Date.now());
      toast.info(r.detections.length ? `Найдено на кадре: ${r.detections.length}` : "На кадре никого не найдено");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // --- редактор ---
  const zone = f.zone ?? [];
  const dir = f.direction;
  const setDir = (patch: Partial<NonNullable<Form["direction"]>>) =>
    set("direction", { line: dir?.line ?? ([] as unknown as [Point, Point]), door_point: dir?.door_point ?? (null as unknown as Point), margin: dir?.margin ?? 0.02, ...patch });

  const handles: [Handle, Point][] = [
    ...zone.map((p, i) => [{ kind: "zone", i }, p] as [Handle, Point]),
    ...(dir?.line ?? []).map((p, i) => [{ kind: "line", i: i as 0 | 1 }, p] as [Handle, Point]),
    ...(dir?.door_point ? [[{ kind: "door" }, dir.door_point] as [Handle, Point]] : []),
  ];

  const moveHandle = (h: Handle, p: Point) => {
    if (h.kind === "zone") set("zone", zone.map((q, i) => (i === h.i ? p : q)));
    else if (h.kind === "line") {
      const line = [...(dir?.line ?? [])] as [Point, Point];
      line[h.i] = p;
      setDir({ line });
    } else setDir({ door_point: p });
  };

  const onDown = (p: Point) => {
    const hit = handles.find(([, q]) => Math.hypot(q[0] - p[0], q[1] - p[1]) < 0.02);
    if (hit) return setDrag(hit[0]);
    if (mode === "zone") set("zone", [...zone, p]);
    else if (mode === "line") {
      const line: Point[] = dir?.line ?? [];
      setDir({ line: (line.length >= 2 ? [p] : [...line, p]) as unknown as [Point, Point] });
    } else if (mode === "door") setDir({ door_point: p });
  };

  const toggleSpecies = (s: Species) =>
    set("species", f.species.includes(s) ? f.species.filter((x) => x !== s) : [...f.species, s]);

  return (
    <div>
      <div className="row between mb">
        <h1 style={{ margin: 0 }}><Link to="/cameras">Камеры</Link> / {isNew ? "новая" : f.name}</h1>
        <div className="row">
          {!isNew && <button className="danger" onClick={remove}><Icon path={mdiDeleteOutline} size={18} />Удалить</button>}
          <button className="primary" onClick={save} disabled={busy}><Icon path={mdiContentSaveOutline} size={18} />Сохранить</button>
        </div>
      </div>
      {error && <div className="error mb">{error}</div>}

      <div className="label-layout" style={{ gridTemplateColumns: "minmax(0, 1fr) 360px" }}>
        <div className="panel stack">
          {isNew ? (
            f.nvr_id && f.source_url === null ? (
              <NewCameraChannels nvrId={f.nvr_id} selected={f.channel} onSelect={(c) => {
                setSaved(false);
                setF((x) => {
                  if (!x) return x;
                  const autoName = !x.name || channelNames.current.has(x.name);
                  const name = autoName ? c.name || `Канал ${c.channel}` : x.name;
                  channelNames.current.add(name);
                  return { ...x, channel: c.channel, name, slug: slugTouched ? x.slug : translit(name) };
                });
              }} />
            ) : (
              <div className="muted">Сохраните камеру, чтобы увидеть кадр и нарисовать зону и линию направления.</div>
            )
          ) : (
            <>
              <div className="row">
                {(["none", "zone", "line", "door"] as Mode[]).map((m) => (
                  <button key={m} className={mode === m ? "active" : ""} onClick={() => setMode(m)}>
                    <Icon size={18} path={{ none: mdiCursorDefault, zone: mdiVectorPolygon, line: mdiVectorLine, door: mdiDoorOpen }[m]} />
                    {{ none: "Указатель", zone: "Зона", line: "Линия", door: "Точка двери" }[m]}
                  </button>
                ))}
                <span style={{ flex: 1 }} />
                <button className="ghost" onClick={() => set("zone", null)} disabled={!f.zone}><Icon path={mdiEraser} size={18} />Сбросить зону</button>
                <button className="ghost" onClick={() => set("direction", null)} disabled={!f.direction}><Icon path={mdiEraser} size={18} />Сбросить линию</button>
              </div>
              <div className="small muted">{HINT[mode]}</div>
              {live ? (
                <LiveView cameraId={+id!} />
              ) : snapError ? (
                <div className="error">Нет кадра с камеры. Проверьте регистратор/канал и сохраните настройки.</div>
              ) : (
                <ImageCanvas
                  src={`/api/cameras/${id}/snapshot?v=${snapVer}`}
                  onError={() => setSnapError(true)}
                  onLoad={() => setSnapError(false)}
                  cursor={mode === "none" ? "default" : "crosshair"}
                  onDown={onDown}
                  onMove={(p) => drag && moveHandle(drag, p)}
                  onUp={() => setDrag(null)}
                >
                  {(g) => (
                    <>
                      {zone.length > 0 && (
                        <polygon points={zone.map((p) => `${g.x(p[0])},${g.y(p[1])}`).join(" ")}
                          fill="rgba(0,200,0,0.12)" stroke="#2ecc40" strokeWidth={g.px(2)} strokeDasharray={zone.length < 3 ? `${g.px(6)} ${g.px(4)}` : undefined} />
                      )}
                      {dir?.line?.length === 2 && (
                        <line x1={g.x(dir.line[0][0])} y1={g.y(dir.line[0][1])} x2={g.x(dir.line[1][0])} y2={g.y(dir.line[1][1])} stroke="#e040fb" strokeWidth={g.px(3)} />
                      )}
                      {dir?.door_point && (
                        <text x={g.x(dir.door_point[0]) + g.px(10)} y={g.y(dir.door_point[1]) + g.px(5)} fill="#e040fb" fontSize={g.px(14)} fontWeight={700}>дверь</text>
                      )}
                      {handles.map(([h, p], i) => (
                        <circle key={i} cx={g.x(p[0])} cy={g.y(p[1])} r={g.px(h.kind === "door" ? 7 : 5)}
                          fill={h.kind === "zone" ? "#2ecc40" : "#e040fb"} stroke="#fff" strokeWidth={g.px(1.5)} style={{ cursor: "move" }} />
                      ))}
                      {dets?.map((d, i) => (
                        <g key={`d${i}`}>
                          <rect x={g.x(d.box[0])} y={g.y(d.box[1])} width={g.x(d.box[2] - d.box[0])} height={g.y(d.box[3] - d.box[1])}
                            fill="none" stroke={d.species === "cat" ? "#f0a020" : "#3a8ee6"} strokeWidth={g.px(2)} />
                          <text x={g.x(d.box[0]) + g.px(3)} y={g.y(d.box[1]) + g.px(14)} fill="#fff" fontSize={g.px(13)} stroke="#000" strokeWidth={g.px(0.4)}>
                            {SPECIES_LABEL[d.species]} {Math.round((d.conf ?? 0) * 100)}%
                          </text>
                        </g>
                      ))}
                    </>
                  )}
                </ImageCanvas>
              )}
              <div className="row">
                <button className={live ? "active" : ""} onClick={() => setLive(!live)}>
                  <Icon path={live ? mdiStop : mdiPlay} size={18} />{live ? "Остановить просмотр" : "Живой просмотр детекции"}
                </button>
                {!live && <button onClick={() => (setSnapVer(Date.now()), setSnapError(false), setDets(null))}><Icon path={mdiRefresh} size={18} />Обновить кадр</button>}
                {!live && <button onClick={testDetect}><Icon path={mdiImageSearchOutline} size={18} />Тест детекции на кадре</button>}
                {dets && <span className="small muted">{dets.length ? `найдено: ${dets.length}` : "ничего не найдено"}</span>}
              </div>
            </>
          )}
        </div>

        <div className="panel stack">
          <label className="field">Название<input value={f.name} onChange={(e) => { set("name", e.target.value); if (!slugTouched) set("slug", translit(e.target.value)); }} /></label>
          <label className="field">
            Идентификатор (для сущностей HA)
            <input className="mono" value={f.slug} onChange={(e) => { setSlugTouched(true); set("slug", e.target.value); }} placeholder="front_door" />
          </label>
          <label className="field">
            Регистратор
            <select value={f.source_url !== null ? "url" : f.nvr_id ?? ""} onChange={(e) => {
              if (e.target.value === "url") { set("source_url", ""); set("nvr_id", null); }
              else { set("source_url", null); set("nvr_id", +e.target.value); }
            }}>
              {nvrs?.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
              <option value="url">Свой URL (RTSP / видеофайл)</option>
            </select>
          </label>
          {f.source_url !== null ? (
            <label className="field">URL источника<input className="mono" value={f.source_url} onChange={(e) => set("source_url", e.target.value)} placeholder="rtsp://… или путь к видео" /></label>
          ) : (
            <div className="form-grid">
              <label className="field">Канал<input type="number" min={1} value={f.channel} onChange={num("channel")} /></label>
              <label className="field">
                Поток
                <select value={f.stream} onChange={(e) => set("stream", e.target.value as Form["stream"])}>
                  <option value="sub">дополнительный</option>
                  <option value="main">основной</option>
                </select>
              </label>
            </div>
          )}
          <AspectField value={f.aspect} cameraId={isNew ? null : +id!} onChange={(v) => set("aspect", v)} />
          <label className="field">
            Когда анализировать
            <select value={f.trigger} onChange={(e) => set("trigger", e.target.value as Form["trigger"])} disabled={f.source_url !== null}>
              <option value="motion">по детекции движения регистратора</option>
              <option value="always">постоянно</option>
            </select>
          </label>
          <div className="row">
            <span className="small muted">Искать:</span>
            {(["cat", "dog", "person"] as Species[]).map((s) => (
              <label key={s} className="check">
                <input type="checkbox" checked={f.species.includes(s)} onChange={() => toggleSpecies(s)} />{SPECIES_LABEL[s]}
              </label>
            ))}
          </div>
          <details>
            <summary className="small">Параметры детекции</summary>
            <div className="form-grid" style={{ marginTop: 8 }}>
              <label className="field">Кадров/сек<input type="number" step={0.5} value={f.fps} onChange={num("fps")} /></label>
              <label className="field">Работать после движения, с<input type="number" value={f.linger} onChange={num("linger")} /></label>
              <label className="field">Удерживать «есть», с<input type="number" value={f.clear_after} onChange={num("clear_after")} /></label>
              <label className="field">Мин. уверенность кадра<input type="number" step={0.05} value={f.min_conf} onChange={num("min_conf")} /></label>
              <label className="field">Кадров для подтверждения<input type="number" value={f.confirm_hits} onChange={num("confirm_hits")} /></label>
              <label className="field">Уверенность подтверждения<input type="number" step={0.05} value={f.confirm_conf} onChange={num("confirm_conf")} /></label>
              <label className="field">Порог узнавания объекта<input type="number" step={0.05} value={f.identity_conf} onChange={num("identity_conf")} /></label>
              {f.direction && (
                <label className="field">Мёртвая зона у линии<input type="number" step={0.01} value={f.direction.margin} onChange={(e) => setDir({ margin: +e.target.value })} /></label>
              )}
            </div>
          </details>
          <label className="check"><input type="checkbox" checked={f.save_frames} onChange={(e) => set("save_frames", e.target.checked)} />Сохранять кадры с найденными объектами для разметки</label>
          <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} />Камера включена</label>
        </div>
      </div>
    </div>
  );
}

function NewCameraChannels({ nvrId, selected, onSelect }: { nvrId: number; selected: number; onSelect: (c: ChannelInfo) => void }) {
  const [channels, setChannels] = useState<ChannelInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { data: cams } = useApi<Camera[]>("/api/cameras");

  useEffect(() => {
    setChannels(null);
    setError(null);
    api<{ ok: boolean; error?: string; channels?: ChannelInfo[] }>(`/api/nvrs/${nvrId}/test`, { method: "POST" })
      .then((r) => (r.ok ? setChannels(r.channels ?? []) : setError(r.error ?? "Нет связи с регистратором")))
      .catch((e) => setError(e.message));
  }, [nvrId]);

  if (error) return <div className="error">{error}</div>;
  if (!channels) return <div className="muted">Загружаю каналы регистратора…</div>;
  if (!channels.length) return <div className="muted">Регистратор не сообщил список каналов — укажите номер канала справа.</div>;
  return (
    <div className="stack">
      <div className="small muted">Выберите камеру. После сохранения можно будет нарисовать зону и линию направления.</div>
      <ChannelGrid nvrId={nvrId} channels={channels} cameras={cams ?? []} selected={selected} onSelect={onSelect} />
    </div>
  );
}

/** Кадры с детекциями примерно раз в секунду: видно, что находит модель и почему событие не сработало. */
function LiveView({ cameraId }: { cameraId: number }) {
  const [src, setSrc] = useState(`/api/cameras/${cameraId}/debug?v=${Date.now()}`);
  const [error, setError] = useState(false);
  const timer = useRef<number>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const next = (delay: number) => {
    timer.current = window.setTimeout(() => setSrc(`/api/cameras/${cameraId}/debug?v=${Date.now()}`), delay);
  };
  return (
    <div className="stack" style={{ gap: 6 }}>
      {error && <div className="error">Нет кадра: камера выключена, поток недоступен или модель ещё загружается. Повторяю…</div>}
      <img className="full-image" src={src} style={{ maxHeight: "70vh", margin: 0 }} alt=""
        onLoad={() => (setError(false), next(700))} onError={() => (setError(true), next(3000))} />
      <div className="small muted">
        Цветные рамки — детекции, которые идут в трекер; <b>OK</b> — подтверждённый трек (будет событие).
        Серые: <span className="mono">weak</span> — уверенность ниже порога кадра,{" "}
        <span className="mono">out-of-zone</span> — центр рамки вне зоны. Внизу видно, есть ли движение и идёт ли анализ.
      </div>
    </div>
  );
}

const ASPECTS = ["4:3", "16:9", "1:1", "3:4", "9:16"];

/** Пропорции кадра: аналоговые камеры часто отдают картинку с неквадратными пикселями — она выглядит сплющенной. */
function AspectField({ value, cameraId, onChange }: { value: string | null; cameraId: number | null; onChange: (v: string | null) => void }) {
  const [native, setNative] = useState<[number, number] | null>(null);
  const custom = value !== null && !ASPECTS.includes(value);
  const [customMode, setCustomMode] = useState(custom);

  useEffect(() => {
    if (cameraId === null) return;
    api<{ native: [number, number] | null }>(`/api/cameras/${cameraId}/stream_info`).then((r) => setNative(r.native)).catch(() => {});
  }, [cameraId]);

  const nativeLabel = native ? `${native[0]}×${native[1]}` : "";
  return (
    <div className="stack" style={{ gap: 6 }}>
      <label className="field">
        Пропорции кадра
        <select value={customMode ? "custom" : value ?? ""} onChange={(e) => {
          const v = e.target.value;
          if (v === "custom") { setCustomMode(true); return; }
          setCustomMode(false);
          onChange(v || null);
        }}>
          <option value="">Как в потоке{nativeLabel && ` (${nativeLabel})`}</option>
          {ASPECTS.map((a) => <option key={a} value={a}>{a}</option>)}
          <option value="custom">Свои…</option>
        </select>
      </label>
      {customMode && (
        <input className="mono" placeholder="например 5:4" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} />
      )}
      <div className="small muted">
        Если на картинке всё сплющено или вытянуто (круглое выглядит овальным), выберите пропорции, при которых кадр выглядит естественно.
        Влияет на превью, снимки событий и распознавание; зона и линия не сдвигаются.
      </div>
    </div>
  );
}
