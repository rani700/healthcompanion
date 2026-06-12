import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  api,
  setAuthToken,
  type Demographics,
  type Role,
  type User,
} from "./api";

type AuthState = {
  user: User | null;
  ready: boolean; // finished restoring any saved session
  login: (email: string, password: string) => Promise<void>;
  signup: (
    email: string,
    password: string,
    name: string,
    role: Role,
    extra?: Demographics,
  ) => Promise<void>;
  logout: () => void;
};

const STORAGE_KEY = "hc.token";
const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  // Restore a saved token on first load and validate it.
  useEffect(() => {
    const token = localStorage.getItem(STORAGE_KEY);
    if (!token) {
      setReady(true);
      return;
    }
    setAuthToken(token);
    api
      .me()
      .then(setUser)
      .catch(() => {
        localStorage.removeItem(STORAGE_KEY);
        setAuthToken(null);
      })
      .finally(() => setReady(true));
  }, []);

  const persist = useCallback((token: string, u: User) => {
    localStorage.setItem(STORAGE_KEY, token);
    setAuthToken(token);
    setUser(u);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const { token, user: u } = await api.login(email, password);
      persist(token, u);
    },
    [persist],
  );

  const signup = useCallback(
    async (
      email: string,
      password: string,
      name: string,
      role: Role,
      extra: Demographics = {},
    ) => {
      const { token, user: u } = await api.signup(
        email,
        password,
        name,
        role,
        extra,
      );
      persist(token, u);
    },
    [persist],
  );

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setAuthToken(null);
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, ready, login, signup, logout }),
    [user, ready, login, signup, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
