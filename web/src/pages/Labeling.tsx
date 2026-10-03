import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AnnotationItem, Box, Camera, Identity, ImageItem, Point, Prediction, SPECIES_LABEL, Species, api, fmtTime } from "../api";
import ImageCanvas from "../components/ImageCanvas";
import { useApi } from "../hooks";

const STATUS_LABEL = { unlabeled: "Неразмеченные", labeled: "Размеченные", skipped: "Пропущенные" } as const;
type Status = keyof typeof STATUS_LABEL;

export default function Labeling() {
  const { id } = useParams();
  return id ? <Editor key={id} imageId={+id} /> : <Queue />;
}

// ---------------- очередь ----------------

function Queue() {
  const [qs, setQs] = useSearchParams();
  const status = (qs.get("status") ?? "unlabeled") as Status;
  const camera = qs.get("camera") ?? "";
  const [page, setPage] = useState(0);
  const limit = 60;
  const { data, reload } = useApi<{ total: number; items: ImageItem[] }>(
    `/api/images?status=${status}&limit=${limit}&offset=${page * limit}${camera ? `&camera_id=${camera}` : ""}`,
  );
  const { data: stats, reload: reloadStats } = useApi<{ by_status: Record<string, number> }>("/api/images/stats");
  const { data: cams } = useApi<Camera[]>("/api/cameras");
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    try {
      await api("/api/images/upload", { form });
      reload();
      reloadStats();
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const camName = (cid: number | null) => cams?.find((c) => c.id === cid)?.name ?? "загружено";
  const setParam = (k: string, v: string) => {
    const n = new URLSearchParams(qs);
    if (v) n.set(k, v);
    else n.delete(k);
    setQs(n);
    setPage(0);
  };

  return (
    <div>
      <div className="row between mb">
        <h1 style={{ margin: 0 }}>Разметка</h1>
        <div className="row">
          <input ref={fileRef} type="file" accept="image/*" multiple hidden onChange={(e) => upload(e.target.files)} />
          <button onClick={() => fileRef.current?.click()} disabled={uploading}>{uploading ? "Загрузка…" : "Загрузить фото"}</button>
          {data?.items[0] && <Link to={`/labeling/${data.items[0].id}`}><button className="primary">Начать разметку →</button></Link>}
        </div>
      </div>
      <div className="notice small mb">
        Кадры с животными сохраняются сюда автоматически. Проверьте или поправьте рамки, укажите вид и конкретное животное.
        Кадры без животных (ложные срабатывания) отмечайте «Нет животных»: это тоже полезно для обучения.
      </div>
      <div className="row mb">
        {(Object.keys(STATUS_LABEL) as Status[]).map((s) => (
          <button key={s} className={s === status ? "active" : ""} onClick={() => setParam("status", s)}>
            {STATUS_LABEL[s]} ({stats?.by_status[s] ?? 0})
          </button>
        ))}
        <select style={{ width: 200 }} value={camera} onChange={(e) => setParam("camera", e.target.value)}>
          <option value="">Все камеры</option>
          {cams?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      {data?.items.length === 0 && <div className="muted">Здесь пусто.</div>}
      <div className="thumbs">
        {data?.items.map((img) => (
          <Link to={`/labeling/${img.id}`} key={img.id} className="item">
            <img className="thumb" src={`/api/images/${img.id}/file`} loading="lazy" alt="" />
            <span className="cap badge small">{camName(img.camera_id)} · {fmtTime(img.captured_at)}</span>
          </Link>
        ))}
      </div>
      {data && data.total > limit && (
        <div className="row" style={{ marginTop: 16 }}>
          <button disabled={page === 0} onClick={() => setPage(page - 1)}>←</button>
          <span className="small muted">{page * limit + 1}–{Math.min((page + 1) * limit, data.total)} из {data.total}</span>
          <button disabled={(page + 1) * limit >= data.total} onClick={() => setPage(page + 1)}>→</button>
        </div>
      )}
    </div>
  );
}

// ---------------- редактор ----------------

type Drag =
  | { kind: "new"; start: Point }
  | { kind: "move"; idx: number; start: Point; orig: Box }
  | { kind: "corner"; idx: number; corner: number };

const COLOR: Record<Species, string> = { cat: "#f0a020", dog: "#3a8ee6" };

function normBox(b: Box): Box {
  return [Math.min(b[0], b[2]), Math.min(b[1], b[3]), Math.max(b[0], b[2]), Math.max(b[1], b[3])];
}

function Editor({ imageId }: { imageId: number }) {
  const navigate = useNavigate();
  const [img, setImg] = useState<ImageItem | null>(null);
  const [boxes, setBoxes] = useState<AnnotationItem[]>([]);
  const [fromModel, setFromModel] = useState(false);
  const [sel, setSel] = useState<number | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [draft, setDraft] = useState<Box | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { data: identities } = useApi<Identity[]>("/api/identities");
  const { data: cams } = useApi<Camera[]>("/api/cameras");
  const lastSpecies = useRef<Species>((localStorage.getItem("cd.lastSpecies") as Species) || "cat");

  const fromPredictions = (preds: Prediction[]): AnnotationItem[] =>
    preds.map((p) => ({ box: p.box, species: p.species, identity_id: p.identity_id ?? null }));

  useEffect(() => {
    api<ImageItem>(`/api/images/${imageId}`)
      .then((r) => {
        setImg(r);
        if (r.status === "labeled") {
          setBoxes(r.annotations ?? []);
          setFromModel(false);
        } else {
          setBoxes(fromPredictions(r.predictions ?? []));
          setFromModel((r.predictions ?? []).length > 0);
        }
        setSel(null);
      })
      .catch((e) => setError(e.message));
  }, [imageId]);

  const goNext = useCallback(() => {
    if (img?.next_id) navigate(`/labeling/${img.next_id}`);
    else navigate(`/labeling?status=${img?.status ?? "unlabeled"}`);
  }, [img, navigate]);

  const save = useCallback(async (list: AnnotationItem[]) => {
    setBusy(true);
    try {
      await api(`/api/images/${imageId}/annotations`, { method: "PUT", body: { annotations: list } });
      goNext();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [imageId, goNext]);

  const skip = useCallback(async () => {
    await api(`/api/images/${imageId}/status?value=skipped`, { method: "POST" });
    goNext();
  }, [imageId, goNext]);

  const predict = async () => {
    try {
      const r = await api<{ predictions: Prediction[] }>(`/api/images/${imageId}/predict`, { method: "POST" });
      setBoxes(fromPredictions(r.predictions));
      setFromModel(true);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const removeImage = async () => {
    if (!confirm("Удалить кадр совсем?")) return;
    await api(`/api/images/${imageId}`, { method: "DELETE" });
    goNext();
  };

  const update = (idx: number, patch: Partial<AnnotationItem>) => {
    setBoxes((bs) => bs.map((b, i) => (i === idx ? { ...b, ...patch } : b)));
    if (patch.species) {
      lastSpecies.current = patch.species;
      localStorage.setItem("cd.lastSpecies", patch.species);
    }
  };
  const del = (idx: number) => {
    setBoxes((bs) => bs.filter((_, i) => i !== idx));
    setSel(null);
  };

  // горячие клавиши
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName.match(/INPUT|SELECT|TEXTAREA/)) return;
      if (e.key === "Enter") { e.preventDefault(); save(boxes); }
      else if (e.key === "n" || e.key === "т") save([]);
      else if (e.key === "s" || e.key === "ы") skip();
      else if (e.key === "ArrowRight" && img?.next_id) navigate(`/labeling/${img.next_id}`);
      else if (e.key === "ArrowLeft" && img?.prev_id) navigate(`/labeling/${img.prev_id}`);
      else if (sel !== null) {
        if (e.key === "Delete" || e.key === "Backspace") del(sel);
        else if (e.key === "c" || e.key === "с") update(sel, { species: "cat" });
        else if (e.key === "d" || e.key === "в") update(sel, { species: "dog" });
        else if (e.key === "0") update(sel, { identity_id: null });
        else if (/^[1-9]$/.test(e.key) && identities?.[+e.key - 1]) {
          const ident = identities[+e.key - 1];
          update(sel, { identity_id: ident.id, species: ident.species });
        } else if (e.key === "Escape") setSel(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const hitCorner = (p: Point): { idx: number; corner: number } | null => {
    if (sel === null || !boxes[sel]) return null;
    const b = boxes[sel].box;
    const corners: Point[] = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]];
    const c = corners.findIndex((q) => Math.hypot(q[0] - p[0], q[1] - p[1]) < 0.015);
    return c >= 0 ? { idx: sel, corner: c } : null;
  };
  const hitBox = (p: Point) => {
    // самый маленький бокс под курсором — чтобы можно было выбрать вложенный
    let best = -1;
    let area = Infinity;
    boxes.forEach((b, i) => {
      const [x1, y1, x2, y2] = b.box;
      if (p[0] >= x1 && p[0] <= x2 && p[1] >= y1 && p[1] <= y2 && (x2 - x1) * (y2 - y1) < area) {
        best = i;
        area = (x2 - x1) * (y2 - y1);
      }
    });
    return best;
  };

  const onDown = (p: Point) => {
    const corner = hitCorner(p);
    if (corner) return setDrag({ kind: "corner", ...corner });
    const idx = hitBox(p);
    if (idx >= 0) {
      setSel(idx);
      return setDrag({ kind: "move", idx, start: p, orig: boxes[idx].box });
    }
    setSel(null);
    setDrag({ kind: "new", start: p });
    setDraft([p[0], p[1], p[0], p[1]]);
  };
  const onMove = (p: Point) => {
    if (!drag) return;
    if (drag.kind === "new") setDraft([drag.start[0], drag.start[1], p[0], p[1]]);
    else if (drag.kind === "move") {
      const dx = p[0] - drag.start[0];
      const dy = p[1] - drag.start[1];
      const o = drag.orig;
      update(drag.idx, { box: [o[0] + dx, o[1] + dy, o[2] + dx, o[3] + dy] });
    } else {
      const b = [...boxes[drag.idx].box] as Box;
      if (drag.corner === 0 || drag.corner === 3) b[0] = p[0]; else b[2] = p[0];
      if (drag.corner === 0 || drag.corner === 1) b[1] = p[1]; else b[3] = p[1];
      update(drag.idx, { box: b });
    }
  };
  const onUp = () => {
    if (drag?.kind === "new" && draft) {
      const b = normBox(draft);
      if (b[2] - b[0] > 0.01 && b[3] - b[1] > 0.01) {
        setBoxes((bs) => [...bs, { box: b, species: lastSpecies.current, identity_id: null }]);
        setSel(boxes.length);
      }
    } else if (drag && drag.kind !== "new") {
      update(drag.idx, { box: normBox(boxes[drag.idx].box) });
    }
    setDrag(null);
    setDraft(null);
  };

  if (error && !img) return <div className="error">{error}</div>;
  if (!img) return <div className="muted">Загрузка…</div>;
  const identName = (iid: number | null) => identities?.find((i) => i.id === iid)?.name;

  return (
    <div>
      <div className="row between mb">
        <h1 style={{ margin: 0 }}>
          <Link to={`/labeling?status=${img.status}`}>Разметка</Link> / кадр #{img.id}{" "}
          <span className="badge small">{STATUS_LABEL[img.status]}</span>
        </h1>
        <div className="row">
          <button disabled={!img.prev_id} onClick={() => navigate(`/labeling/${img.prev_id}`)}>← Новее</button>
          <button disabled={!img.next_id} onClick={() => navigate(`/labeling/${img.next_id}`)}>Старее →</button>
        </div>
      </div>
      {error && <div className="error mb">{error}</div>}
      <div className="label-layout">
        <div className="panel">
          <ImageCanvas src={`/api/images/${img.id}/file`} cursor="crosshair" onDown={onDown} onMove={onMove} onUp={onUp}>
            {(g) => (
              <>
                {boxes.map((b, i) => {
                  const [x1, y1, x2, y2] = b.box;
                  const name = identName(b.identity_id) ?? SPECIES_LABEL[b.species];
                  return (
                    <g key={i}>
                      <rect x={g.x(x1)} y={g.y(y1)} width={g.x(x2 - x1)} height={g.y(y2 - y1)} fill={i === sel ? "rgba(255,255,255,0.12)" : "none"}
                        stroke={COLOR[b.species]} strokeWidth={g.px(i === sel ? 3 : 2)} strokeDasharray={fromModel ? `${g.px(6)} ${g.px(3)}` : undefined} />
                      <text x={g.x(x1) + g.px(3)} y={g.y(y1) - g.px(4)} fill={COLOR[b.species]} fontSize={g.px(14)} fontWeight={700} stroke="#000" strokeWidth={g.px(0.4)}>
                        {i + 1}. {name}
                      </text>
                      {i === sel && [[x1, y1], [x2, y1], [x2, y2], [x1, y2]].map(([cx, cy], k) => (
                        <rect key={k} x={g.x(cx) - g.px(5)} y={g.y(cy) - g.px(5)} width={g.px(10)} height={g.px(10)} fill="#fff" stroke={COLOR[b.species]} strokeWidth={g.px(1.5)} />
                      ))}
                    </g>
                  );
                })}
                {draft && (
                  <rect x={g.x(Math.min(draft[0], draft[2]))} y={g.y(Math.min(draft[1], draft[3]))} width={g.x(Math.abs(draft[2] - draft[0]))}
                    height={g.y(Math.abs(draft[3] - draft[1]))} fill="none" stroke="#fff" strokeWidth={g.px(1.5)} strokeDasharray={`${g.px(5)} ${g.px(3)}`} />
                )}
              </>
            )}
          </ImageCanvas>
          <div className="small muted" style={{ marginTop: 8 }}>
            {cams?.find((c) => c.id === img.camera_id)?.name ?? "загружено вручную"} · {fmtTime(img.captured_at)} · {img.width}×{img.height}
            {fromModel && " · пунктир — предложения модели, проверьте их"}
          </div>
        </div>

        <div className="stack">
          <div className="panel stack">
            <h3>Животные на кадре ({boxes.length})</h3>
            {boxes.length === 0 && <div className="small muted">Нарисуйте рамку мышью вокруг животного.</div>}
            {boxes.map((b, i) => (
              <div key={i} className="stack" style={{ gap: 6, padding: 8, borderRadius: 6, border: `1px solid ${i === sel ? "var(--accent)" : "var(--border)"}` }}
                onClick={() => setSel(i)}>
                <div className="row between">
                  <b>{i + 1}.</b>
                  <button className="small danger" onClick={(e) => (e.stopPropagation(), del(i))}>удалить</button>
                </div>
                <div className="row">
                  {(["cat", "dog"] as Species[]).map((s) => (
                    <button key={s} className={`small ${b.species === s ? "active" : ""}`} onClick={() => update(i, { species: s })}>{SPECIES_LABEL[s]}</button>
                  ))}
                </div>
                <select value={b.identity_id ?? ""} onChange={(e) => {
                  const iid = e.target.value ? +e.target.value : null;
                  const ident = identities?.find((x) => x.id === iid);
                  update(i, { identity_id: iid, ...(ident ? { species: ident.species } : {}) });
                }}>
                  <option value="">— не знаю, кто это —</option>
                  {identities?.map((ident, k) => (
                    <option key={ident.id} value={ident.id}>{k < 9 ? `${k + 1}: ` : ""}{ident.name}</option>
                  ))}
                </select>
              </div>
            ))}
          </div>

          <div className="panel stack">
            <button className="primary" onClick={() => save(boxes)} disabled={busy}>Сохранить и дальше <span className="kbd">Enter</span></button>
            <button onClick={() => save([])} disabled={busy}>Нет животных <span className="kbd">N</span></button>
            <button onClick={skip} disabled={busy}>Пропустить <span className="kbd">S</span></button>
            <button onClick={predict}>Предразметка моделью</button>
            <button className="danger" onClick={removeImage}>Удалить кадр</button>
          </div>

          <div className="panel small muted stack" style={{ gap: 4 }}>
            <div><b>Мышь:</b> тяните по пустому месту, чтобы нарисовать рамку; клик выбирает рамку, углы меняют её размер.</div>
            <div><span className="kbd">1</span>…<span className="kbd">9</span> — объект, <span className="kbd">0</span> — неизвестный</div>
            <div><span className="kbd">C</span> кошка, <span className="kbd">D</span> собака, <span className="kbd">Del</span> удалить</div>
            <div><span className="kbd">←</span> <span className="kbd">→</span> соседние кадры</div>
          </div>
        </div>
      </div>
    </div>
  );
}
