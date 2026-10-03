import { useState } from "react";
import { Link } from "react-router-dom";
import type { Camera } from "../api";
import { mdiCheck, mdiPlus } from "@mdi/js";
import Icon from "./Icon";
import Modal from "./Modal";

export interface ChannelInfo {
  channel: number;
  name: string;
}

interface Props {
  nvrId: number;
  channels: ChannelInfo[];
  cameras?: Camera[];
  /** выбранный канал (режим выбора в форме камеры) */
  selected?: number;
  onSelect?: (c: ChannelInfo) => void;
  /** кнопка «Добавить» (режим страницы регистраторов) */
  onAdd?: (c: ChannelInfo) => void;
  version?: number;
}

/** Сетка каналов регистратора с живыми превью — чтобы было видно, какая камера на каком канале. */
export default function ChannelGrid({ nvrId, channels, cameras, selected, onSelect, onAdd, version = 0 }: Props) {
  const [failed, setFailed] = useState<Record<number, boolean>>({});
  const [zoom, setZoom] = useState<ChannelInfo | null>(null);
  const src = (ch: number) => `/api/nvrs/${nvrId}/channels/${ch}/snapshot?v=${version}`;

  return (
    <>
      <div className="thumbs">
        {channels.map((c) => {
          const cam = cameras?.find((x) => x.nvr_id === nvrId && x.channel === c.channel && !x.source_url);
          return (
            <div key={c.channel} className={`item channel ${selected === c.channel ? "selected" : ""}`}
              onClick={() => onSelect?.(c)} style={{ cursor: onSelect ? "pointer" : undefined }}>
              {failed[c.channel] ? (
                <div className="thumb muted small" style={{ display: "flex", alignItems: "center", justifyContent: "center", background: "var(--bg)" }}>
                  нет изображения
                </div>
              ) : (
                <img className="thumb" src={src(c.channel)} loading="lazy" alt=""
                  onError={() => setFailed((f) => ({ ...f, [c.channel]: true }))}
                  onClick={(e) => { if (!onSelect) { e.stopPropagation(); setZoom(c); } }} />
              )}
              <div className="row between" style={{ padding: "6px 2px 2px", gap: 4 }}>
                <div className="small" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  <b>{c.channel}</b>{c.name && ` · ${c.name}`}
                </div>
                {cam ? (
                  <Link to={`/cameras/${cam.id}`} className="badge ok small" onClick={(e) => e.stopPropagation()}><Icon path={mdiCheck} size={12} />добавлена</Link>
                ) : onAdd ? (
                  <button className="small primary" onClick={(e) => { e.stopPropagation(); onAdd(c); }}><Icon path={mdiPlus} size={14} />Добавить</button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
      {zoom && (
        <Modal title={`Канал ${zoom.channel}${zoom.name ? ` · ${zoom.name}` : ""}`} onClose={() => setZoom(null)} wide>
          <img className="full-image" src={src(zoom.channel)} alt="" />
          {onAdd && !cameras?.some((x) => x.nvr_id === nvrId && x.channel === zoom.channel && !x.source_url) && (
            <div className="row" style={{ marginTop: 12 }}>
              <button className="primary" onClick={() => onAdd(zoom)}><Icon path={mdiPlus} size={18} />Добавить эту камеру</button>
            </div>
          )}
        </Modal>
      )}
    </>
  );
}
