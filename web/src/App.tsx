import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Nvrs from "./pages/Nvrs";
import Cameras from "./pages/Cameras";
import CameraEdit from "./pages/CameraEdit";
import Identities from "./pages/Identities";
import Labeling from "./pages/Labeling";
import Training from "./pages/Training";
import Events from "./pages/Events";
import Settings from "./pages/Settings";

interface AuthStatus {
  setup_required: boolean;
  authenticated: boolean;
  username: string | null;
}

const NAV = [
  ["/", "Обзор"],
  ["/nvrs", "Регистраторы"],
  ["/cameras", "Камеры"],
  ["/identities", "Объекты"],
  ["/labeling", "Разметка"],
  ["/training", "Обучение"],
  ["/events", "События"],
  ["/settings", "Настройки"],
] as const;

export default function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);

  const refresh = useCallback(() => {
    api<AuthStatus>("/api/auth/status").then(setAuth).catch(() => setAuth(null));
  }, []);

  useEffect(() => {
    refresh();
    const onUnauth = () => refresh();
    window.addEventListener("catdetect:unauthorized", onUnauth);
    return () => window.removeEventListener("catdetect:unauthorized", onUnauth);
  }, [refresh]);

  if (!auth) return <div className="center-page muted">Загрузка…</div>;
  if (!auth.authenticated) return <Login setup={auth.setup_required} onDone={refresh} />;

  const logout = async () => {
    await api("/api/auth/logout", { method: "POST" });
    refresh();
  };

  return (
    <div className="layout">
      <nav className="sidebar">
        <div className="brand">🐈 CatDetect</div>
        {NAV.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>
            {label}
          </NavLink>
        ))}
        <div className="spacer" />
        <div className="status">
          {auth.username} · <a href="#" onClick={(e) => (e.preventDefault(), logout())}>выйти</a>
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/nvrs" element={<Nvrs />} />
          <Route path="/cameras" element={<Cameras />} />
          <Route path="/cameras/:id" element={<CameraEdit />} />
          <Route path="/identities" element={<Identities />} />
          <Route path="/labeling" element={<Labeling />} />
          <Route path="/labeling/:id" element={<Labeling />} />
          <Route path="/training" element={<Training />} />
          <Route path="/events" element={<Events />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}
