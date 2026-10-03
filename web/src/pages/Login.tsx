import { FormEvent, useState } from "react";
import { mdiCat } from "@mdi/js";
import { api } from "../api";
import Icon from "../components/Icon";

export default function Login({ setup, onDone }: { setup: boolean; onDone: () => void }) {
  const [username, setUsername] = useState(setup ? "admin" : "");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (setup && password !== password2) return setError("Пароли не совпадают");
    setBusy(true);
    setError(null);
    try {
      await api(setup ? "/api/auth/setup" : "/api/auth/login", { body: { username, password } });
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="center-page">
      <form className="panel stack" onSubmit={submit}>
        <div className="login-logo">
          <div className="mark"><Icon path={mdiCat} size={32} /></div>
          <h1>Cat<span className="gradient-text">Detect</span></h1>
        </div>
        {setup && <div className="notice">Первый запуск: создайте учётную запись администратора.</div>}
        <label className="field">
          Логин
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus={!setup} autoComplete="username" />
        </label>
        <label className="field">
          Пароль{setup && " (минимум 8 символов)"}
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus={setup}
            autoComplete={setup ? "new-password" : "current-password"} />
        </label>
        {setup && (
          <label className="field">
            Повторите пароль
            <input type="password" value={password2} onChange={(e) => setPassword2(e.target.value)} autoComplete="new-password" />
          </label>
        )}
        {error && <div className="error">{error}</div>}
        <button className="primary" disabled={busy} style={{ justifyContent: "center" }}>{setup ? "Создать" : "Войти"}</button>
      </form>
    </div>
  );
}
