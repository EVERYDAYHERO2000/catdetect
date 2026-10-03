import { useState } from "react";
import { Species, api } from "../api";
import { mdiContentSaveOutline } from "@mdi/js";
import Icon from "./Icon";
import Modal from "./Modal";

export type FolderFields = { name: string; species: Species; is_own: boolean; notes: string };

/** Папка = объект распознавания: конкретное животное/человек или группа («Чужие кошки»). */
export default function FolderForm({ initial, onClose, onSaved }: {
  initial: { id: number | null; form: FolderFields };
  onClose: () => void;
  onSaved: (id: number) => void;
}) {
  const [f, setF] = useState(initial.form);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    try {
      const r = await api<{ id: number }>(initial.id ? `/api/identities/${initial.id}` : "/api/identities", {
        method: initial.id ? "PUT" : "POST",
        body: f,
      });
      onSaved(r.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <Modal title={initial.id ? "Папка" : "Новая папка"} onClose={onClose}>
      <div className="stack">
        <label className="field">
          Имя
          <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus placeholder="Барсик, Илья, Чужие кошки…"
            onKeyDown={(e) => e.key === "Enter" && f.name && save()} />
        </label>
        <div className="row">
          <label className="field" style={{ flex: 1 }}>
            Кто это
            <select value={f.species} onChange={(e) => setF({ ...f, species: e.target.value as Species })}>
              <option value="cat">Кошка</option>
              <option value="dog">Собака</option>
              <option value="person">Человек</option>
            </select>
          </label>
          <label className="check"><input type="checkbox" checked={f.is_own} onChange={(e) => setF({ ...f, is_own: e.target.checked })} />Свой</label>
        </div>
        <label className="field">
          Заметки
          <textarea rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} placeholder="Рыжий, белые лапы" />
        </label>
        {error && <div className="error">{error}</div>}
        <div className="row"><button className="primary" onClick={save} disabled={!f.name}><Icon path={mdiContentSaveOutline} size={18} />Сохранить</button></div>
      </div>
    </Modal>
  );
}
