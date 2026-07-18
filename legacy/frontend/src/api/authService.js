import { api, setConfig, setAccessToken, clearAccessToken } from './apiClient'
import { useAuthStore } from '@/store/authStore'
import API_ENDPOINTS from './apiEndpoints'

/**
 * Inicializa el apiClient con la config del proyecto.
 * La baseURL se toma por defecto de VITE_API_URL en apiClient.js.
 * Llamar UNA vez en el entry point (main.jsx) antes de cualquier request.
 */
export function initApiClient() {
  setConfig({
    refreshEndpoint: API_ENDPOINTS.auth.refresh,
    loginEndpoint: API_ENDPOINTS.auth.login,
    onUnauthorized: () => {
      useAuthStore.getState().logout()
    },
    onTokenChange: (access) => {
      if (access) useAuthStore.getState().setAccess(access)
    },
  })

  const persisted = useAuthStore.getState().access
  if (persisted) setAccessToken(persisted)
}

/**
 * Login: POST /api/auth/token
 * Devuelve { access, user } y guarda en el store.
 * El refresh token se setea automáticamente como cookie HttpOnly.
 */
export async function login(email, password) {
  const data = await api.post(API_ENDPOINTS.auth.login, { email, password })
  useAuthStore.getState().login(data.user, data.access)
  setAccessToken(data.access)
  return data
}

/**
 * Logout: borra cookie en el server + limpia store local.
 */
export async function logout() {
  try {
    await api.post(API_ENDPOINTS.auth.logout, {})
  } catch {
  } finally {
    useAuthStore.getState().logout()
    clearAccessToken()
  }
}

/**
 * Get current user: GET /api/users/me/
 */
export async function getMe() {
  const data = await api.get(API_ENDPOINTS.user.me)
  useAuthStore.getState().setUser(data)
  return data
}
