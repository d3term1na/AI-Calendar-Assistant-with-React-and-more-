import { useAuth } from "./AuthContext";

export const useAuthFetch = () => {
  const { accessToken, setAccessToken } = useAuth();

  const authFetch = async (url, options = {}) => {
    let res = await fetch(url, {
      ...options,
      headers: {
        ...options.headers,
        Authorization: `Bearer ${accessToken}`,
      },
      credentials: "include",
    });

    if (res.status === 401) {
      const refreshRes = await fetch("/refresh", {
        method: "POST",
        credentials: "include",
      });

      if (!refreshRes.ok) {
        setAccessToken(null);
        return res;
      }

      const data = await refreshRes.json();
      setAccessToken(data.access_token);

      res = await fetch(url, {
        ...options,
        headers: {
          ...options.headers,
          Authorization: `Bearer ${data.access_token}`,
        },
        credentials: "include",
      });
    }

    return res;
  };
  return authFetch;
};