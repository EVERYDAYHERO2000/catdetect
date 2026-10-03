import { DragEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Camera, Identity, SPECIES_LABEL, Species, api, fmtTime } from "../api";
import {
  mdiArrowRight, mdiCheckAll, mdiDeleteOutline, mdiFolderPlusOutline, mdiImageRemove, mdiInboxArrowDown, mdiPencilOutline,
  mdiSchool, mdiSelectionRemove, mdiTrayArrowUp,
} from "@mdi/js";
import { ReactNode } from "react";
import FolderForm, { FolderFields } from "../components/FolderForm";
import Icon from "../components/Icon";
import SpeciesIcon from "../components/SpeciesIcon";
import { useToast } from "../components/Toast";
import { useApi } from "../hooks";

interface Folder extends Identity {
  count: number;
}

interface Summary {
  pending: number;
  rejected: number;
  empty_frames: number;
  first_empty_frame_id: number | null;
  folders: Folder[];
  training: { min_samples: number; ready_folders: number; can_train: boolean; new_since_last: number; has_model: boolean };
}

interface Crop {
  id: number;
  image_id: number;
  camera_id: number | null;
  captured_at: string;
  species: Species;
  conf: number | null;
  identity_id: number | null;
  suggested_identity_id: number | null;
}

const GROUP: Record<Species, string> = { cat: "Кошки", dog: "Собаки", person: "Люди" };
const PAGE = 120;

type Target = "pending" | "rejected" | number;

function plural(n: number, one: string, few: string, many: string) {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
}

export default function Objects() {
  const [qs, setQs] = useSearchParams();
  const folder = qs.get("folder") ?? "pending";
  const navigate = useNavigate();
  const { data: summary, reload: reloadSummary } = useApi<Summary>("/api/objects/summary");
  const { data: cams } = useApi<Camera[]>("/api/cameras");
  const [crops, setCrops] = useState<Crop[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [lastClicked, setLastClicked] = useState<number | null>(null);
  const [edit, setEdit] = useState<{ id: number | null; form: FolderFields } | null>(null);
  const [dropHover, setDropHover] = useState<string | null>(null);
  const toast = useToast();
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const folders = summary?.folders ?? [];
  const current = folders.find((f) => String(f.id) === folder);

  const loadCrops = useCallback(async (append = false) => {
    const offset = append ? crops.length : 0;
    const r = await api<{ total: number; items: Crop[] }>(`/api/objects/crops?folder=${folder}&limit=${PAGE}&offset=${offset}`);
    setCrops((c) => (append ? [...c, ...r.items] : r.items));
    setTotal(r.total);
    if (!append) setSelected(new Set());
  }, [folder, crops.length]);

  useEffect(() => {
    loadCrops(false);
  }, [folder]); // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = () => {
    reloadSummary();
    loadCrops(false);
  };

  const openFolder = (f: string) => {
    setQs({ folder: f });
  };

  const folderName = (t: Target) =>
    t === "pending" ? "Неразобранные" : t === "rejected" ? "Не объект" : folders.find((f) => f.id === t)?.name ?? "?";

  const moveTo = async (target: Target, ids?: number[]) => {
    const list = ids ?? [...selected];
    if (!list.length) return;
    const r = await api<{ moved: number }>("/api/objects/move", { body: { ids: list, target } });
    toast.success(`${r.moved} ${plural(r.moved, "снимок", "снимка", "снимков")} → «${folderName(target)}»`);
    refresh();
  };

  const acceptSuggestions = async () => {
    const ids = selected.size ? [...selected] : crops.filter((c) => c.suggested_identity_id).map((c) => c.id);
    const r = await api<{ moved: number }>("/api/objects/accept_suggestions", { body: { ids } });
    toast.success(r.moved ? `Разложено по подсказкам: ${r.moved}` : "Подсказок для выбранных снимков нет");
    refresh();
  };

  const toggle = (id: number, shift: boolean) => {
    setSelected((s) => {
      const n = new Set(s);
      if (shift && lastClicked !== null) {
        const a = crops.findIndex((c) => c.id === lastClicked);
        const b = crops.findIndex((c) => c.id === id);
        crops.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((c) => n.add(c.id));
      } else if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
    setLastClicked(id);
  };

  // порядок папок для горячих клавиш 1…9
  const ordered = useMemo(() => {
    const out: Folder[] = [];
    (["cat", "dog", "person"] as Species[]).forEach((sp) => out.push(...folders.filter((f) => f.species === sp)));
    return out;
  }, [folders]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName.match(/INPUT|SELECT|TEXTAREA/) || edit) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "a") {
        e.preventDefault();
        setSelected(new Set(crops.map((c) => c.id)));
      } else if (e.key === "Escape") setSelected(new Set());
      else if (!selected.size) return;
      else if (/^[1-9]$/.test(e.key) && ordered[+e.key - 1]) moveTo(ordered[+e.key - 1].id);
      else if (e.key === "x" || e.key === "ч" || e.key === "Delete" || e.key === "Backspace") moveTo("rejected");
      else if (e.key === "u" || e.key === "г") moveTo("pending");
      else if (e.key === "Enter") acceptSuggestions();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // drag & drop карточек на папку слева
  const onDragStart = (e: DragEvent, c: Crop) => {
    const ids = selected.has(c.id) ? [...selected] : [c.id];
    e.dataTransfer.setData("text/plain", JSON.stringify(ids));
    e.dataTransfer.effectAllowed = "move";
  };
  const dropProps = (key: string, target: Target) => ({
    onDragOver: (e: DragEvent) => (e.preventDefault(), setDropHover(key)),
    onDragLeave: () => setDropHover((h) => (h === key ? null : h)),
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      setDropHover(null);
      try {
        moveTo(target, JSON.parse(e.dataTransfer.getData("text/plain")));
      } catch {
        /* не наши данные */
      }
    },
  });

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    try {
      const target = current ? `?identity_id=${current.id}` : "";
      const r = await api<{ ids: number[]; found: number; assigned: number }>(`/api/images/upload${target}`, { form });
      if (current) {
        toast.success(`Загружено ${r.ids.length}, добавлено в «${current.name}»: ${r.assigned}`);
        if (r.assigned < r.ids.length) toast.info("На части фото модель не нашла подходящий объект — они в «Неразобранных» или среди фото без объектов");
      } else {
        toast.success(`Загружено фото: ${r.ids.length}, найдено объектов: ${r.found}`);
      }
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const removeFolder = async (f: Folder) => {
    if (!confirm(`Удалить папку «${f.name}»? Снимки из неё вернутся в «Неразобранные».`)) return;
    await api(`/api/identities/${f.id}`, { method: "DELETE" });
    toast.success(`Папка «${f.name}» удалена`);
    openFolder("pending");
    reloadSummary();
  };

  const camName = (id: number | null) => (id ? cams?.find((c) => c.id === id)?.name : null) ?? "фото";
  const identName = (id: number | null) => folders.find((f) => f.id === id)?.name;
  const t = summary?.training;
  const hasSuggestions = folder === "pending" && crops.some((c) => c.suggested_identity_id);

  const FolderItem = ({ k, target, icon, label, count, warn }: { k: string; target: Target; icon: ReactNode; label: string; count: number; warn?: boolean }) => (
    <div className={`folder ${folder === k ? "active" : ""} ${dropHover === k ? "drop" : ""}`} onClick={() => openFolder(k)} {...dropProps(k, target)}>
      {icon}
      <span className="name">{label}</span>
      <span className={`count ${warn ? "warn" : ""}`}>{count}</span>
    </div>
  );

  return (
    <div>
      <div className="row between mb">
        <h1 style={{ margin: 0 }}>Объекты</h1>
        <div className="row">
          <input ref={fileRef} type="file" accept="image/*" multiple hidden onChange={(e) => upload(e.target.files)} />
          <button onClick={() => fileRef.current?.click()} disabled={uploading}>
            <Icon path={mdiTrayArrowUp} size={18} />{uploading ? "Загрузка…" : current ? `Загрузить фото в «${current.name}»` : "Загрузить фото"}
          </button>
          <button className="primary" onClick={() => setEdit({ id: null, form: { name: "", species: "cat", is_own: true, notes: "" } })}><Icon path={mdiFolderPlusOutline} size={18} />Новая папка</button>
        </div>
      </div>

      {t && t.can_train && t.new_since_last >= 10 && (
        <div className="notice mb row between">
          <span>
            {t.has_model ? `С последнего обучения разложено ${t.new_since_last} новых снимков` : "Снимков уже достаточно для первого обучения"} — можно
            {t.has_model ? " переобучить" : " обучить"} распознавание.
          </span>
          <Link to="/settings?tab=models"><button className="small primary"><Icon path={mdiSchool} size={16} />Перейти к обучению</button></Link>
        </div>
      )}

      <div className="objects-layout">
        <div className="panel folders">
          <FolderItem k="pending" target="pending" icon={<Icon path={mdiInboxArrowDown} size={18} />} label="Неразобранные" count={summary?.pending ?? 0} />
          {(["cat", "dog", "person"] as Species[]).map((sp) => {
            const list = folders.filter((f) => f.species === sp);
            if (!list.length) return null;
            return (
              <div key={sp}>
                <div className="group">{GROUP[sp]}</div>
                {list.map((f) => (
                  <FolderItem key={f.id} k={String(f.id)} target={f.id} icon={<SpeciesIcon species={sp} />} label={f.name + (f.is_own ? "" : " (чужие)")}
                    count={f.count} warn={f.count < (t?.min_samples ?? 5)} />
                ))}
              </div>
            );
          })}
          <div className="group" />
          <FolderItem k="rejected" target="rejected" icon={<Icon path={mdiImageRemove} size={18} />} label="Не объект" count={summary?.rejected ?? 0} />
          {folders.length === 0 && (
            <div className="small muted" style={{ padding: 8 }}>
              Создайте папки для своих животных и людей: «Барсик», «Мурка», «Илья». Пригодится и папка «Чужие кошки».
            </div>
          )}
        </div>

        <div className="stack">
          <div className="panel stack" style={{ gap: 8 }}>
            <div className="row between">
              <div>
                <h2 className="title-row" style={{ margin: 0 }}>
                  {folder === "pending" ? <><Icon path={mdiInboxArrowDown} />Неразобранные</>
                    : folder === "rejected" ? <><Icon path={mdiImageRemove} />Не объект</>
                    : current ? <><SpeciesIcon species={current.species} size={20} />{current.name}</> : "Папка"}
                  <span className="muted" style={{ fontWeight: 400 }}>· {total}</span>
                </h2>
                <div className="small muted">
                  {folder === "pending" && "Сюда попадают все, кого нашла модель. Выделите снимки и переложите в папку: кнопками ниже, перетаскиванием или клавишами."}
                  {folder === "rejected" && "Ошибки модели: тени, коврики, пакеты. Эти снимки учат детектор не путать их с животными и людьми."}
                  {current && `${SPECIES_LABEL[current.species]}${current.is_own ? "" : ", чужие"}${current.notes ? ` · ${current.notes}` : ""}` +
                    (current.count < (t?.min_samples ?? 5) ? ` · для обучения нужно хотя бы ${t?.min_samples ?? 5} снимков, лучше 30+` : "")}
                </div>
              </div>
              {current && (
                <div className="row">
                  <button className="small" onClick={() => setEdit({ id: current.id, form: { name: current.name, species: current.species, is_own: current.is_own, notes: current.notes } })}><Icon path={mdiPencilOutline} size={16} />Изменить</button>
                  <button className="small danger" onClick={() => removeFolder(current)}><Icon path={mdiDeleteOutline} size={16} />Удалить папку</button>
                </div>
              )}
            </div>
            {folder === "pending" && summary && summary.empty_frames > 0 && summary.first_empty_frame_id && (
              <div className="small">
                Ещё {summary.empty_frames} фото, на которых модель никого не нашла —{" "}
                <Link to={`/objects/frame/${summary.first_empty_frame_id}`}>обвести вручную</Link>.
              </div>
            )}
          </div>

          {(selected.size > 0 || hasSuggestions) && (
            <div className="panel toolbar">
              <span className="small"><b>{selected.size ? `Выбрано: ${selected.size}` : "Ничего не выбрано"}</b></span>
              {selected.size > 0 && ordered.map((f, i) => (
                String(f.id) !== folder && (
                  <button key={f.id} className="small" onClick={() => moveTo(f.id)}>
                    {i < 9 && <span className="kbd">{i + 1}</span>}<Icon path={mdiArrowRight} size={14} />{f.name}
                  </button>
                )
              ))}
              {selected.size > 0 && folder !== "rejected" && <button className="small danger" onClick={() => moveTo("rejected")}><span className="kbd">X</span><Icon path={mdiImageRemove} size={14} />Не объект</button>}
              {selected.size > 0 && folder !== "pending" && <button className="small" onClick={() => moveTo("pending")}><span className="kbd">U</span><Icon path={mdiInboxArrowDown} size={14} />В неразобранные</button>}
              {hasSuggestions && (
                <button className="small primary" onClick={acceptSuggestions}>
                  <span className="kbd">Enter</span><Icon path={mdiCheckAll} size={14} />{selected.size ? "Принять подсказки у выбранных" : "Принять все подсказки"}
                </button>
              )}
              {selected.size > 0 && <button className="small ghost" onClick={() => setSelected(new Set())}><Icon path={mdiSelectionRemove} size={14} />Снять выделение</button>}
            </div>
          )}

          {crops.length === 0 ? (
            <div className="panel muted">
              {folder === "pending" ? "Неразобранных снимков нет. Новые появятся, когда камеры кого-нибудь увидят." : "В папке пока пусто. Переложите сюда снимки из «Неразобранных» или загрузите фото."}
            </div>
          ) : (
            <div className="crops-grid">
              {crops.map((c) => {
                const sug = c.suggested_identity_id && folder === "pending" ? identName(c.suggested_identity_id) : null;
                return (
                  <div key={c.id} className={`crop ${selected.has(c.id) ? "selected" : ""}`} draggable
                    onDragStart={(e) => onDragStart(e, c)}
                    onClick={(e) => toggle(c.id, e.shiftKey)}
                    onDoubleClick={() => navigate(`/objects/frame/${c.image_id}`)}
                    title="Клик — выделить, Shift — диапазон, двойной клик — открыть кадр целиком">
                    <img src={`/api/identities/crops/${c.id}?img=${c.image_id}`} loading="lazy" alt="" draggable={false} />
                    {sug && <span className="chip">{sug}?</span>}
                    <div className="cap">
                      <span><SpeciesIcon species={c.species} size={12} /> {camName(c.camera_id)}</span>
                      <span>{fmtTime(c.captured_at)}{c.conf ? ` · ${Math.round(c.conf * 100)}%` : ""}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {crops.length < total && <button onClick={() => loadCrops(true)}>Показать ещё ({total - crops.length})</button>}
          <div className="small muted">
            Клавиши: <span className="kbd">1</span>…<span className="kbd">9</span> — в папку по порядку, <span className="kbd">X</span> — не объект,{" "}
            <span className="kbd">U</span> — в неразобранные, <span className="kbd">Enter</span> — принять подсказки,{" "}
            <span className="kbd">⌘A</span> — выделить все. Двойной клик открывает кадр целиком, там можно поправить рамку.
          </div>
        </div>
      </div>

      {edit && (
        <FolderForm initial={edit} onClose={() => setEdit(null)} onSaved={(id) => {
          toast.success(edit.id ? "Папка сохранена" : `Папка «${edit.form.name}» создана`);
          setEdit(null);
          reloadSummary();
          if (!edit.id) openFolder(String(id));
        }} />
      )}
    </div>
  );
}
