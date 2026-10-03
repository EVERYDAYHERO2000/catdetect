import { useState } from "react";
import { Link } from "react-router-dom";
import { Identity, SPECIES_LABEL, Species, api } from "../api";
import Modal from "../components/Modal";
import { useApi } from "../hooks";

type Form = { name: string; species: Species; is_own: boolean; notes: string };

export default function Identities() {
  const { data, reload, error } = useApi<Identity[]>("/api/identities");
  const [edit, setEdit] = useState<{ id: number | null; form: Form } | null>(null);

  const remove = async (i: Identity) => {
    if (!confirm(`Удалить «${i.name}»? Разметка останется, но без привязки к объекту.`)) return;
    await api(`/api/identities/${i.id}`, { method: "DELETE" });
    reload();
  };

  return (
    <div>
      <div className="row between mb">
        <h1 style={{ margin: 0 }}>Объекты</h1>
        <button className="primary" onClick={() => setEdit({ id: null, form: { name: "", species: "cat", is_own: true, notes: "" } })}>+ Добавить</button>
      </div>
      <div className="notice mb small">
        Объект — конкретное животное («Барсик») или группа («Чужие кошки»). Детектор находит кошек и собак, а затем
        классификатор узнаёт, кто это. Чтобы его обучить, разметьте не меньше 5 (лучше 30 и больше) кадров на каждый объект
        на странице <Link to="/labeling">«Разметка»</Link>. Нужно минимум 2 объекта; объект «Чужие кошки» помогает не путать
        своих с соседскими.
      </div>
      {error && <div className="error mb">{error}</div>}
      <div className="grid">
        {data?.map((i) => (
          <div className="panel stack" key={i.id}>
            <div className="row between">
              <h3 style={{ margin: 0 }}>{i.name}</h3>
              <div className="row">
                <span className={`badge ${i.species}`}>{SPECIES_LABEL[i.species]}</span>
                <span className="badge">{i.is_own ? "свой" : "чужой"}</span>
              </div>
            </div>
            <Samples id={i.id} count={i.samples} />
            {i.notes && <div className="small muted">{i.notes}</div>}
            <div className="row">
              <button onClick={() => setEdit({ id: i.id, form: { name: i.name, species: i.species, is_own: i.is_own, notes: i.notes } })}>Изменить</button>
              <button className="danger" onClick={() => remove(i)}>Удалить</button>
            </div>
          </div>
        ))}
      </div>
      {edit && <IdentityForm initial={edit} onClose={() => setEdit(null)} onSaved={() => (setEdit(null), reload())} />}
    </div>
  );
}

function Samples({ id, count }: { id: number; count: number }) {
  const { data } = useApi<{ annotation_id: number; image_id: number }[]>(count ? `/api/identities/${id}/samples?limit=12` : null, [count]);
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="small muted">Размечено примеров: <b>{count}</b>{count < 5 && " (для обучения нужно ≥ 5)"}</div>
      {data && data.length > 0 && (
        <div className="crops">
          {data.map((s) => (
            <Link key={s.annotation_id} to={`/labeling/${s.image_id}`}>
              <img src={`/api/identities/crops/${s.annotation_id}`} alt="" loading="lazy" />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function IdentityForm({ initial, onClose, onSaved }: { initial: { id: number | null; form: Form }; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState(initial.form);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    try {
      await api(initial.id ? `/api/identities/${initial.id}` : "/api/identities", { method: initial.id ? "PUT" : "POST", body: f });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <Modal title={initial.id ? "Объект" : "Новый объект"} onClose={onClose}>
      <div className="stack">
        <label className="field">Имя<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus placeholder="Барсик" /></label>
        <div className="row">
          <label className="field" style={{ flex: 1 }}>
            Вид
            <select value={f.species} onChange={(e) => setF({ ...f, species: e.target.value as Species })}>
              <option value="cat">Кошка</option>
              <option value="dog">Собака</option>
            </select>
          </label>
          <label className="check"><input type="checkbox" checked={f.is_own} onChange={(e) => setF({ ...f, is_own: e.target.checked })} />Своё животное</label>
        </div>
        <label className="field">Заметки<textarea rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} placeholder="Рыжий, белые лапы" /></label>
        {error && <div className="error">{error}</div>}
        <div className="row"><button className="primary" onClick={save} disabled={!f.name}>Сохранить</button></div>
      </div>
    </Modal>
  );
}
