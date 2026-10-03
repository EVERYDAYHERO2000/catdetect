import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Camera, Nvr, Point, Prediction, SPECIES_LABEL, Species, api } from "../api";
import ImageCanvas from "../components/ImageCanvas";
import { useApi } from "../hooks";

type Form = Omit<Camera, "id">;
type Mode = "none" | "zone" | "line" | "door";
type Handle = { kind: "zone"; i: number } | { kind: "line"; i: 0 | 1 } | { kind: "door" };

const DEFAULTS: Form = {
  slug: "", name: "", nvr_id: null, channel: 1, stream: "sub", source_url: null, trigger: "motion", fps: 5, linger: 10,
  clear_after: 10, species: ["cat", "dog"], zone: null, direction: null, min_conf: 0.25, confirm_hits: 3,
  confirm_conf: 0.5, identity_conf: 0.6, save_frames: true, enabled: true,
};

const HINT: Record<Mode, string> = {
  none: "Перетаскивайте точки мышью. Выберите инструмент, чтобы рисовать.",
  zone: "Кликайте, чтобы добавить вершины зоны. Животные вне зоны игнорируются.",
  line: "Поставьте две точки линии, которую животное пересекает по пути к двери.",
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
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (isNew) {
      const name = qs.get("name") ?? "";
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
      if (isNew) navigate(`/cameras/${r.id}`, { replace: true });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!confirm(`Удалить камеру «${f.name}»? История событий будет удалена, кадры для обучения сохранятся.`)) return;
    await api(`/api/cameras/${id}`, { method: "DELETE" });
    navigate("/cameras");
  };

  const testDetect = async () => {
    setDets(null);
    try {
      const r = await api<{ detections: Prediction[] }>(`/api/cameras/${id}/detect`, { method: "POST" });
      setDets(r.detections);
      setSnapVer(Date.now());
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
          {saved && <span className="badge ok">сохранено</span>}
          {!isNew && <button className="danger" onClick={remove}>Удалить</button>}
          <button className="primary" onClick={save} disabled={busy}>Сохранить</button>
        </div>
      </div>
      {error && <div className="error mb">{error}</div>}

      <div className="label-layout" style={{ gridTemplateColumns: "minmax(0, 1fr) 360px" }}>
        <div className="panel stack">
          {isNew ? (
            <div className="muted">Сохраните камеру, чтобы увидеть кадр и нарисовать зону и линию направления.</div>
          ) : (
            <>
              <div className="row">
                {(["none", "zone", "line", "door"] as Mode[]).map((m) => (
                  <button key={m} className={mode === m ? "active" : ""} onClick={() => setMode(m)}>
                    {{ none: "Указатель", zone: "Зона", line: "Линия", door: "Точка двери" }[m]}
                  </button>
                ))}
                <span style={{ flex: 1 }} />
                <button onClick={() => set("zone", null)} disabled={!f.zone}>Сбросить зону</button>
                <button onClick={() => set("direction", null)} disabled={!f.direction}>Сбросить линию</button>
              </div>
              <div className="small muted">{HINT[mode]}</div>
              {snapError ? (
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
                <button onClick={() => (setSnapVer(Date.now()), setSnapError(false), setDets(null))}>Обновить кадр</button>
                <button onClick={testDetect}>Тест детекции на кадре</button>
                {dets && <span className="small muted">{dets.length ? `найдено: ${dets.length}` : "животных не найдено"}</span>}
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
          <label className="field">
            Когда анализировать
            <select value={f.trigger} onChange={(e) => set("trigger", e.target.value as Form["trigger"])} disabled={f.source_url !== null}>
              <option value="motion">по детекции движения регистратора</option>
              <option value="always">постоянно</option>
            </select>
          </label>
          <div className="row">
            <span className="small muted">Искать:</span>
            {(["cat", "dog"] as Species[]).map((s) => (
              <label key={s} className="check"><input type="checkbox" checked={f.species.includes(s)} onChange={() => toggleSpecies(s)} />{SPECIES_LABEL[s]}</label>
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
              <label className="field">Порог узнавания животного<input type="number" step={0.05} value={f.identity_conf} onChange={num("identity_conf")} /></label>
              {f.direction && (
                <label className="field">Мёртвая зона у линии<input type="number" step={0.01} value={f.direction.margin} onChange={(e) => setDir({ margin: +e.target.value })} /></label>
              )}
            </div>
          </details>
          <label className="check"><input type="checkbox" checked={f.save_frames} onChange={(e) => set("save_frames", e.target.checked)} />Сохранять кадры с животными для разметки</label>
          <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} />Камера включена</label>
        </div>
      </div>
    </div>
  );
}
