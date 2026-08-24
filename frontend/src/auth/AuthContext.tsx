import { api } from "@/api/endpoints";
import { isSessionInvalidError, resetCsrfToken } from "@/api/client";
import type { CurrentUser } from "@/api/types";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

interface AuthContextValue {
  user: CurrentUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<CurrentUser>;
  register: (email: string, username: string, password: string, accessCode?: string) => Promise<CurrentUser>;
  logout: () => Promise<void>;
  refresh: () => Promise<CurrentUser | null>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const current = await api.auth.me();
      setUser(current);
      return current;
    } catch (error) {
      if (isSessionInvalidError(error)) {
        setUser(null);
        return null;
      }
      throw error;
    }
  }, []);

  useEffect(() => {
    refresh().catch(() => setUser(null)).finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    const handleUnauthorized = () => setUser(null);
    window.addEventListener("alpha:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("alpha:unauthorized", handleUnauthorized);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    resetCsrfToken();
    const response = await api.auth.login(email, password);
    const current = response?.id ? response : await api.auth.me();
    setUser(current);
    return current;
  }, []);

  const register = useCallback(async (email: string, username: string, password: string, accessCode?: string) => {
    resetCsrfToken();
    const response = await api.auth.register(email, username, password, accessCode);
    const current = response?.id ? response : await api.auth.me();
    setUser(current);
    return current;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
      resetCsrfToken();
      setUser(null);
    } catch (error) {
      if (isSessionInvalidError(error)) {
        resetCsrfToken();
        setUser(null);
        return;
      }
      throw error;
    }
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, register, logout, refresh }),
    [user, loading, login, register, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}
