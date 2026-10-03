import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import {
  mdiBellOutline, mdiCat, mdiCctv, mdiChevronDoubleLeft, mdiChevronDoubleRight, mdiCogOutline, mdiFolderMultipleImage,
  mdiLogout, mdiServerNetwork, mdiViewDashboardOutline,
} from "@mdi/js";
import { api } from "./api";
import Icon from "./components/Icon";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Nvrs from "./pages/Nvrs";
import Cameras from "./pages/Cameras";
import CameraEdit from "./pages/CameraEdit";
import Objects from "./pages/Objects";
import FrameEditor from "./pages/FrameEditor";
import Events from "./pages/Events";
import Settings from "./pages/Settings";

interface AuthStatus {
  setup_required: boolean;
  authenticated: boolean;
  username: string | null;
}

const NAV = [
  ["/", "Обзор", mdiViewDashboardOutline],
  ["/objects", "Объекты", mdiFolderMultipleImage],
  ["/events", "События", mdiBellOutline],
  ["/cameras", "Камеры", mdiCctv],
  ["/nvrs", "Регистраторы", mdiServerNetwork],
  ["/settings", "Настройки", mdiCogOutline],
] as const;

export default function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [open, setOpen] = useState(() => localStorage.getItem("cd.sidebar") === "open");
  const toggle = () => setOpen((o) => (localStorage.setItem("cd.sidebar", o ? "closed" : "open"), !o));

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
      <nav className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand">
          <Icon path={mdiCat} size={24} />
          <span className="label">Cat<span className="gradient-text">Detect</span></span>
        </div>
        <button className="side-btn toggle" onClick={toggle} title={open ? "Свернуть" : "Развернуть"}>
          <Icon path={open ? mdiChevronDoubleLeft : mdiChevronDoubleRight} />
          <span className="label">Свернуть</span>
        </button>
        {NAV.map(([to, label, icon]) => (
          <NavLink key={to} to={to} end={to === "/"} title={label}>
            <Icon path={icon} />
            <span className="label">{label}</span>
          </NavLink>
        ))}
        <div className="spacer" />
        <div className="user label">{auth.username}</div>
        <button className="side-btn" onClick={logout} title="Выйти">
          <Icon path={mdiLogout} />
          <span className="label">Выйти</span>
        </button>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/nvrs" element={<Nvrs />} />
          <Route path="/cameras" element={<Cameras />} />
          <Route path="/cameras/:id" element={<CameraEdit />} />
          <Route path="/objects" element={<Objects />} />
          <Route path="/objects/frame/:id" element={<FrameEditor />} />
          {/* старые адреса */}
          <Route path="/identities" element={<Navigate to="/objects" />} />
          <Route path="/labeling/*" element={<Navigate to="/objects" />} />
          <Route path="/training" element={<Navigate to="/settings?tab=models" />} />
          <Route path="/events" element={<Events />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}
