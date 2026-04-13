import { createContext, useContext, useState, useEffect } from "react";

const AuthContext = createContext();

export const AuthProvider = ({ children }) => {
  const [accessToken, setAccessToken] = useState(null);
  const [user, setUser] = useState(null);
  const refresh = async () => {
    const res = await fetch("/refresh", {
      method: "POST",
      credentials: "include",
    });

    if (!res.ok) return null;

    const data = await res.json();
    return data.access_token;
  };

  useEffect(() => {
    const initAuth = async () => {
      const token = await refresh();
      if (!token) {
        setAccessToken(null);
        setUser(null);
        return;
      };

      setAccessToken(token);

      const meRes = await fetch("/me", {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      const me = await meRes.json();
      setUser(me);
    };

    initAuth();
  }, []);

  return (
    <AuthContext.Provider value={{ accessToken, setAccessToken, user, setUser }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }

  return context;
};