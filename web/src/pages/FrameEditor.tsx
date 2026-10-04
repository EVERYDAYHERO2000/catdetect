import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ALL_SPECIES, AnnotationItem, imageFile, Box, Camera, Identity, ImageItem, Point, Prediction, SPECIES_LABEL, Species, api, fmtTime } from "../api";
import { mdiArrowLeft, mdiAutoFix, mdiContentSaveOutline, mdiDeleteOutline, mdiImageOffOutline } from "@mdi/js";
import Hint from "../components/Hint";
import Icon from "../components/Icon";
import ImageCanvas from "../components/ImageCanvas";
import { useToast } from "../components/Toast";
import { useApi } from "../hooks";


/** Кадр целиком: поправить рамки, обвести пропущенное, отметить «никого нет». */
export default function FrameEditor() {
  const { id } = useParams();
  return <Editor key={id} imageId={+id!} />;
}

// ---------------- редактор ----------------

type Drag =
  | { kind: "new"; start: Point }
  | { kind: "move"; idx: number; start: Point; orig: Box }
  | { kind: "corner"; idx: number; corner: number };

const COLOR: Record<Species, string> = { cat: "#f0a020", dog: "#3a8ee6", person: "#78dc3c" };

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

  const toast = useToast();
  const goNext = useCallback(() => {
    if (window.history.length > 1) navigate(-1);
    else navigate("/objects");
  }, [navigate]);

  const save = useCallback(async (list: AnnotationItem[]) => {
    setBusy(true);
    try {
      await api(`/api/images/${imageId}/annotations`, { method: "PUT", body: { annotations: list } });
      toast.success(list.length ? `Кадр сохранён: объектов ${list.length}` : "Отмечено: на кадре никого нет");
      goNext();
    } catch (e) {
      setError((e as Error).message);
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [imageId, goNext, toast]);

  const predict = async () => {
    try {
      const r = await api<{ predictions: Prediction[] }>(`/api/images/${imageId}/predict`, { method: "POST" });
      setBoxes(fromPredictions(r.predictions));
      setFromModel(true);
      toast.info(r.predictions.length ? `Модель нашла: ${r.predictions.length}` : "Модель никого не нашла");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const removeImage = async () => {
    if (!confirm("Удалить кадр совсем?")) return;
    await api(`/api/images/${imageId}`, { method: "DELETE" });
    toast.success("Кадр удалён");
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
      else if (sel !== null) {
        if (e.key === "Delete" || e.key === "Backspace") del(sel);
        else if (e.key === "c" || e.key === "с") update(sel, { species: "cat" });
        else if (e.key === "d" || e.key === "в") update(sel, { species: "dog" });
        else if (e.key === "p" || e.key === "з") update(sel, { species: "person" });
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
          <Link to="/objects">Объекты</Link> / кадр #{img.id}
        </h1>
        <div className="row">
          <button onClick={goNext}><Icon path={mdiArrowLeft} size={18} />Назад</button>
        </div>
      </div>
      {error && <div className="error mb">{error}</div>}
      <div className="label-layout">
        <div className="panel">
          <ImageCanvas src={imageFile(img)} cursor="crosshair" onDown={onDown} onMove={onMove} onUp={onUp}>
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
            <h3 className="label-text">Объекты на кадре ({boxes.length})<Hint>
              Мышь: тяните по пустому месту, чтобы нарисовать рамку; клик выбирает рамку, углы меняют её размер.
              Клавиши: <span className="kbd">1</span>…<span className="kbd">9</span> — папка, <span className="kbd">0</span> — без папки,{" "}
              <span className="kbd">C</span> кошка, <span className="kbd">D</span> собака, <span className="kbd">P</span> человек,{" "}
              <span className="kbd">Del</span> удалить. Рамка без выбранной папки попадёт в «Неразобранные».
            </Hint></h3>
            {boxes.length === 0 && <div className="small muted">Нарисуйте рамку мышью вокруг животного или человека.</div>}
            {boxes.map((b, i) => (
              <div key={i} className="stack" style={{ gap: 6, padding: 8, borderRadius: 6, border: `1px solid ${i === sel ? "var(--accent)" : "var(--border)"}` }}
                onClick={() => setSel(i)}>
                <div className="row between">
                  <b>{i + 1}.</b>
                  <button className="small danger" onClick={(e) => (e.stopPropagation(), del(i))}>удалить</button>
                </div>
                <div className="row">
                  {ALL_SPECIES.map((s) => (
                    <button key={s} className={`small ${b.species === s ? "active" : ""}`} onClick={() => update(i, { species: s })}>{SPECIES_LABEL[s]}</button>
                  ))}
                </div>
                <select value={b.identity_id ?? ""} onChange={(e) => {
                  const iid = e.target.value ? +e.target.value : null;
                  const ident = identities?.find((x) => x.id === iid);
                  update(i, { identity_id: iid, ...(ident ? { species: ident.species } : {}) });
                }}>
                  <option value="">— в «Неразобранные» —</option>
                  {identities?.map((ident, k) => (
                    <option key={ident.id} value={ident.id}>{k < 9 ? `${k + 1}: ` : ""}{ident.name}</option>
                  ))}
                </select>
              </div>
            ))}
          </div>

          <div className="panel stack">
            <button className="primary" onClick={() => save(boxes)} disabled={busy}><Icon path={mdiContentSaveOutline} size={18} />Сохранить <span className="kbd">Enter</span></button>
            <button onClick={() => save([])} disabled={busy}><Icon path={mdiImageOffOutline} size={18} />Нет объектов <span className="kbd">N</span></button>
            <button onClick={predict}><Icon path={mdiAutoFix} size={18} />Предразметка моделью</button>
            <button className="danger" onClick={removeImage}><Icon path={mdiDeleteOutline} size={18} />Удалить кадр</button>
          </div>

        </div>
      </div>
    </div>
  );
}
