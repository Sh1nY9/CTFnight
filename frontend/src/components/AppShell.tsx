import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/components/Toast";
import { getErrorMessage } from "@/lib/errors";
import { cx } from "@/lib/utils";
import {
  Bell,
  Boxes,
  ChevronRight,
  Flag,
  Gauge,
  LogIn,
  LogOut,
  Menu,
  Settings,
  ShieldCheck,
  Trophy,
  UserCog,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";

const participantLinks = [
  { to: "/challenges", label: "문제", icon: Flag },
  { to: "/scoreboard", label: "점수판", icon: Trophy },
  { to: "/team", label: "팀", icon: UsersRound, auth: true },
];

const adminLinks = [
  { to: "/admin", label: "운영 개요", icon: Gauge, end: true },
  { to: "/admin/challenges", label: "문제 관리", icon: Boxes },
  { to: "/admin/submissions", label: "제출 감사", icon: ShieldCheck },
  { to: "/admin/users", label: "사용자 관리", icon: UserCog },
  { to: "/admin/announcements", label: "공지 관리", icon: Bell },
  { to: "/admin/settings", label: "이벤트 설정", icon: Settings },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const { push } = useToast();
  const location = useLocation();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => setMenuOpen(false), [location.pathname]);

  const handleLogout = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await logout();
      navigate("/");
    } catch (error) {
      push(`로그아웃하지 못했습니다. ${getErrorMessage(error)}`, "error");
    } finally {
      setLoggingOut(false);
    }
  };

  return (
    <div className="app-frame">
      <header className="site-header">
        <div className="site-header__inner">
          <Link className="brand" to="/" aria-label="CTFnight 홈">
            <span className="brand__mark" aria-hidden="true">C</span>
            <span>CTF<span className="brand__accent">night</span></span>
          </Link>

          <nav className="desktop-nav" aria-label="주요 메뉴">
            {participantLinks.map(({ to, label, auth }) =>
              auth && !user ? null : (
                <NavLink className={({ isActive }) => cx("nav-link", isActive && "is-active")} key={to} to={to}>
                  {label}
                </NavLink>
              ),
            )}
          </nav>

          <div className="header-actions">
            {user ? (
              <>
                {user.role === "admin" && (
                  <Link className="admin-shortcut" to="/admin">
                    <ShieldCheck size={16} /> 관리자
                  </Link>
                )}
                <Link className="user-chip" to="/team" title={user.email}>
                  <UserRound size={16} />
                  <span>{user.username}</span>
                </Link>
                <button className="icon-button desktop-only" disabled={loggingOut} onClick={() => void handleLogout()} title={loggingOut ? "로그아웃 처리 중" : "로그아웃"} type="button">
                  <LogOut size={18} />
                  <span className="sr-only">로그아웃</span>
                </button>
              </>
            ) : (
              <Link className="button button--primary button--small desktop-only" to="/login">
                <LogIn size={16} /> 로그인
              </Link>
            )}
            <button
              aria-controls="mobile-navigation"
              aria-expanded={menuOpen}
              aria-label={menuOpen ? "메뉴 닫기" : "메뉴 열기"}
              className="icon-button mobile-menu-button"
              onClick={() => setMenuOpen((value) => !value)}
              type="button"
            >
              {menuOpen ? <X /> : <Menu />}
            </button>
          </div>
        </div>
        {menuOpen && (
          <nav className="mobile-nav" id="mobile-navigation" aria-label="모바일 메뉴">
            {participantLinks.map(({ to, label, icon: Icon, auth }) =>
              auth && !user ? null : (
                <NavLink className={({ isActive }) => cx("mobile-nav__link", isActive && "is-active")} key={to} to={to}>
                  <Icon size={18} /> {label} <ChevronRight size={16} />
                </NavLink>
              ),
            )}
            {user?.role === "admin" && (
              <div className="mobile-nav__section">
                <span>관리자</span>
                {adminLinks.map(({ to, label, icon: Icon, end }) => (
                  <NavLink className={({ isActive }) => cx("mobile-nav__link", isActive && "is-active")} end={end} key={to} to={to}>
                    <Icon size={18} /> {label} <ChevronRight size={16} />
                  </NavLink>
                ))}
              </div>
            )}
            {user ? (
              <button className="mobile-nav__link" disabled={loggingOut} onClick={() => void handleLogout()} type="button">
                <LogOut size={18} /> {loggingOut ? "로그아웃 중…" : "로그아웃"}
              </button>
            ) : (
              <Link className="mobile-nav__link" to="/login"><LogIn size={18} /> 로그인</Link>
            )}
          </nav>
        )}
      </header>

      {user?.password_change_required && (
        <div className="security-banner" role="alert">
          <ShieldCheck size={18} /> 초기 관리자 비밀번호를 사용 중입니다.
          <Link to="/account/security">지금 변경하기</Link>
        </div>
      )}

      <main id="main-content" className="main-content">{children}</main>
      <footer className="site-footer">
        <div><span className="brand__accent">CTFnight</span> · Independent CTF platform</div>
        <div>공정하게 경쟁하고, 책임 있게 탐구하세요.</div>
      </footer>
    </div>
  );
}

export function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <div className="admin-layout">
      <aside className="admin-sidebar" aria-label="관리자 메뉴">
        <p className="eyebrow">CONTROL PLANE</p>
        <nav>
          {adminLinks.map(({ to, label, icon: Icon, end }) => (
            <NavLink className={({ isActive }) => cx("admin-nav-link", isActive && "is-active")} end={end} key={to} to={to}>
              <Icon size={17} /> {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="admin-content">{children}</div>
    </div>
  );
}
