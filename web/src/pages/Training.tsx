import { Fragment, useState } from "react";
import { Identity, MlModel, TrainingJob, api, fmtTime } from "../api";
import { useApi, useInterval } from "../hooks";

const KIND = { detector: "Детектор (кошка/собака)", classifier: "Классификатор (кто именно)" } as const;
const STATUS: Record<TrainingJob["status"], string> = { queued: "в очереди", running: "идёт", done: "готово", failed: "ошибка", cancelled: "отменено" };

interface Stats {
  by_status: Record<string, number>;
  by_species: Record<string, number>;
  by_identity: Record<string, number>;
}

function Metrics({ m }: { m: Record<string, number> | undefined }) {
  if (!m || !Object.keys(m).length) return <span className="muted">—</span>;
  const pick = ["mAP50(B)", "mAP50-95(B)", "precision(B)", "recall(B)", "accuracy_top1"];
  const keys = pick.filter((k) => k in m);
  return (
    <span className="small mono">
      {(keys.length ? keys : Object.keys(m).slice(0, 4)).map((k) => `${k.replace("(B)", "")}=${m[k].toFixed(3)}`).join("  ")}
    </span>
  );
}

export default function Training() {
  const { data: stats } = useApi<Stats>("/api/images/stats");
  const { data: identities } = useApi<Identity[]>("/api/identities");
  const { data: jobs, reload: reloadJobs } = useApi<TrainingJob[]>("/api/training/jobs");
  const { data: models, reload: reloadModels } = useApi<{ models: MlModel[]; runtime: Record<string, unknown> }>("/api/models");
  const [epochs, setEpochs] = useState<Record<string, number>>({ detector: 60, classifier: 40 });
  const [error, setError] = useState<string | null>(null);
  const [openLog, setOpenLog] = useState<number | null>(null);
  const [log, setLog] = useState("");

  const running = jobs?.some((j) => j.status === "running" || j.status === "queued");
  useInterval(() => {
    reloadJobs();
    if (openLog) api<TrainingJob>(`/api/training/jobs/${openLog}`).then((j) => setLog(j.log ?? ""));
  }, running ? 3000 : null);
  useInterval(reloadModels, running ? 10000 : null);

  const start = async (kind: "detector" | "classifier") => {
    setError(null);
    try {
      await api("/api/training/jobs", { body: { kind, epochs: epochs[kind] } });
      reloadJobs();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const showLog = async (id: number) => {
    if (openLog === id) return setOpenLog(null);
    setOpenLog(id);
    setLog((await api<TrainingJob>(`/api/training/jobs/${id}`)).log ?? "");
  };

  const activate = async (m: MlModel) => {
    await api(`/api/models/${m.id}/activate`, { method: "POST" });
    reloadModels();
  };
  const deactivate = async (kind: string) => {
    await api(`/api/models/deactivate?kind=${kind}`, { method: "POST" });
    reloadModels();
  };
  const removeModel = async (m: MlModel) => {
    if (!confirm(`Удалить модель «${m.name}»?`)) return;
    await api(`/api/models/${m.id}`, { method: "DELETE" });
    reloadModels();
  };

  const labeled = stats?.by_status.labeled ?? 0;
  const identReady = identities?.filter((i) => (stats?.by_identity[i.id] ?? 0) >= 5).length ?? 0;
  const runtime = models?.runtime as { status?: string; weights?: string; classifier_id?: number } | undefined;

  return (
    <div>
      <h1>Обучение</h1>
      {error && <div className="error mb">{error}</div>}
      <div className="grid mb">
        <div className="panel stack">
          <h2>{KIND.detector}</h2>
          <div className="small muted">
            Дообучает YOLO на ваших кадрах, например на ракурсе сверху над дверью, где стандартная модель ошибается.
            Нужно ≥ 10 размеченных кадров с животными; хорошо — 200 и больше, вместе с кадрами «нет животных».
          </div>
          <div className="small">
            Размечено кадров: <b>{labeled}</b> · кошек: <b>{stats?.by_species.cat ?? 0}</b> · собак: <b>{stats?.by_species.dog ?? 0}</b>
          </div>
          <div className="row">
            <label className="field" style={{ width: 100 }}>Эпох<input type="number" value={epochs.detector} onChange={(e) => setEpochs({ ...epochs, detector: +e.target.value })} /></label>
            <button className="primary" style={{ alignSelf: "flex-end" }} disabled={!!running || labeled < 10} onClick={() => start("detector")}>Обучить детектор</button>
          </div>
        </div>
        <div className="panel stack">
          <h2>{KIND.classifier}</h2>
          <div className="small muted">
            Учится различать ваших животных по вырезанным фрагментам. Нужно ≥ 2 объектов, у каждого ≥ 5 примеров;
            хорошо — 50 и больше, с разным освещением и в ИК-режиме ночью.
          </div>
          <div className="small">
            {identities?.map((i) => (
              <span key={i.id} className={`badge ${(stats?.by_identity[i.id] ?? 0) >= 5 ? "ok" : ""}`} style={{ marginRight: 4 }}>
                {i.name}: {stats?.by_identity[i.id] ?? 0}
              </span>
            ))}
            {!identities?.length && <span className="muted">объектов нет</span>}
          </div>
          <div className="row">
            <label className="field" style={{ width: 100 }}>Эпох<input type="number" value={epochs.classifier} onChange={(e) => setEpochs({ ...epochs, classifier: +e.target.value })} /></label>
            <button className="primary" style={{ alignSelf: "flex-end" }} disabled={!!running || identReady < 2} onClick={() => start("classifier")}>Обучить классификатор</button>
          </div>
        </div>
      </div>

      <div className="panel mb">
        <h2>Задачи</h2>
        {jobs?.length === 0 && <div className="muted">Обучение ещё не запускалось.</div>}
        <table>
          <tbody>
            {jobs?.map((j) => (
              <Fragment key={j.id}>
                <tr>
                  <td>#{j.id}</td>
                  <td>{KIND[j.kind]}</td>
                  <td>
                    <span className={`badge ${j.status === "done" ? "ok" : j.status === "failed" ? "danger" : j.status === "running" ? "warn" : ""}`}>{STATUS[j.status]}</span>
                  </td>
                  <td style={{ width: "30%" }}>
                    {j.status === "running" && <div className="progress"><div style={{ width: `${j.progress * 100}%` }} /></div>}
                    <div className="small muted">{j.message}</div>
                    {j.params.last_metrics && <Metrics m={j.params.last_metrics} />}
                  </td>
                  <td className="small muted">{fmtTime(j.created_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    <button className="small" onClick={() => showLog(j.id)}>лог</button>{" "}
                    {j.status === "running" && <button className="small danger" onClick={() => api(`/api/training/jobs/${j.id}/cancel`, { method: "POST" }).then(reloadJobs)}>отменить</button>}
                  </td>
                </tr>
                {openLog === j.id && (
                  <tr><td colSpan={6}><pre className="log">{log || "лог пуст"}</pre></td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <div className="row between">
          <h2>Модели</h2>
          <span className="small muted">сейчас работает: {runtime?.weights ?? "—"} ({runtime?.status})</span>
        </div>
        <table>
          <thead><tr><th>Модель</th><th>Тип</th><th>Метрики (валидация)</th><th>Создана</th><th /></tr></thead>
          <tbody>
            <tr>
              <td>Базовая YOLO (COCO)</td><td>{KIND.detector}</td><td className="muted small">стандартная, без дообучения</td><td />
              <td style={{ textAlign: "right" }}>
                {models?.models.some((m) => m.kind === "detector" && m.active)
                  ? <button className="small" onClick={() => deactivate("detector")}>Использовать</button>
                  : <span className="badge on">активна</span>}
              </td>
            </tr>
            {models?.models.map((m) => (
              <tr key={m.id}>
                <td>{m.name}</td>
                <td>{KIND[m.kind]}</td>
                <td><Metrics m={m.metrics} /></td>
                <td className="small muted">{fmtTime(m.created_at)}</td>
                <td style={{ textAlign: "right" }}>
                  {m.active ? (
                    <>
                      <span className="badge on">активна</span>{" "}
                      {m.kind === "classifier" && <button className="small" onClick={() => deactivate("classifier")}>Выключить</button>}
                    </>
                  ) : (
                    <>
                      <button className="small primary" onClick={() => activate(m)}>Активировать</button>{" "}
                      <button className="small danger" onClick={() => removeModel(m)}>удалить</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
