import axios from "axios";

// "/api" on the same address the dashboard is opened from - the backend
// serves the dashboard itself (http://localhost:8000/). During development
// (`npm run dev`), Vite forwards /api to the backend - see vite.config.js.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

const ACCESS_KEY = "securetap_access";
const REFRESH_KEY = "securetap_refresh";

export const tokenStore = {
  getAccess: () => localStorage.getItem(ACCESS_KEY),
  getRefresh: () => localStorage.getItem(REFRESH_KEY),
  setTokens: (access, refresh) => {
    localStorage.setItem(ACCESS_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear: () => {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

const apiClient = axios.create({ baseURL: API_BASE_URL });

apiClient.interceptors.request.use((config) => {
  const token = tokenStore.getAccess();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let refreshInFlight = null;

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    const status = error.response?.status;

    if (status !== 401 || originalRequest._retry || !tokenStore.getRefresh()) {
      if (status === 401) {
        tokenStore.clear();
        window.dispatchEvent(new Event("securetap:logout"));
      }
      return Promise.reject(error);
    }

    originalRequest._retry = true;

    try {
      refreshInFlight =
        refreshInFlight ||
        axios.post(`${API_BASE_URL}/auth/refresh`, { refresh: tokenStore.getRefresh() });
      const { data } = await refreshInFlight;
      tokenStore.setTokens(data.access, data.refresh);
      originalRequest.headers.Authorization = `Bearer ${data.access}`;
      return apiClient(originalRequest);
    } catch (refreshError) {
      tokenStore.clear();
      window.dispatchEvent(new Event("securetap:logout"));
      return Promise.reject(refreshError);
    } finally {
      refreshInFlight = null;
    }
  }
);

export default apiClient;
