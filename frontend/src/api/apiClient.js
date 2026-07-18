/**
 * apiClient.js — Servicio HTTP base con auto-refresh JWT.
 *
 * Diseñado para ser reusable entre proyectos:
 * - Sin dependencias externas (usa fetch nativo).
 * - Sin acoplarse a un store específico (usa callbacks inyectables).
 * - La baseURL se toma por defecto de `VITE_API_URL` (build-time, Vite).
 *   Se acepta cualquier forma:
 *     '' | '/api'                  → relativo al origen actual
 *     'https://api.example.com'    → absoluto
 *     'api.example.com'            → se le antepone el protocolo actual
 *   Se puede sobreescribir en runtime via setConfig({ baseURL }).
 *
 * Uso:
 *   import { api, setConfig } from '@/api/apiClient'
 *
 *   setConfig({
 *     refreshEndpoint: '/api/auth/token/refresh',
 *     loginEndpoint: '/api/auth/token',
 *     onUnauthorized: () => { /* logout, redirect *\/ },
 *     onTokenChange: (access) => { /* persist *\/ },
 *     onError: (err) => { /* toast *\/ },
 *   })
 *
 *   await api.post('/api/users/', { name: 'foo' })
 *   await api.get('/api/users/me/')
 *
 * Convenciones:
 * - El access token se guarda en memoria del módulo (no en localStorage)
 *   y se persiste via onTokenChange.
 * - El refresh token viaja en cookie HttpOnly (no accesible desde JS).
 * - Las requests con cookies usan credentials: 'include'.
 * - En un 401 (no siendo login/refresh), se intenta refresh una vez
 *   y se reintenta la request original. Si el refresh falla, se
 *   llama a onUnauthorized.
 */

let config = {
  baseURL: import.meta.env.VITE_API_URL || '',
  refreshEndpoint: '/api/auth/token/refresh',
  loginEndpoint: '/api/auth/token',
  onUnauthorized: null,
  onTokenChange: null,
  onError: null,
}

let accessToken = null
let isRefreshing = false
let failedQueue = []

export function setConfig(partial) {
  config = { ...config, ...partial }
}

export function setAccessToken(token) {
  accessToken = token
  if (config.onTokenChange) config.onTokenChange(token)
}

export function clearAccessToken() {
  accessToken = null
  if (config.onTokenChange) config.onTokenChange(null)
}

function normalizeBaseURL(baseURL) {
  if (!baseURL) return ''
  if (baseURL.startsWith('//')) return `${typeof window !== 'undefined' ? window.location.protocol : 'https:'}${baseURL}`
  if (/^https?:\/\//i.test(baseURL)) return baseURL
  if (baseURL.startsWith('/')) return baseURL
  const protocol = typeof window !== 'undefined' ? window.location.protocol : 'https:'
  return `${protocol}//${baseURL}`
}

function buildUrl(path) {
  if (path.startsWith('http')) return path
  return `${normalizeBaseURL(config.baseURL)}${path}`
}

async function parseResponse(response) {
  const text = await response.text()
  const data = text ? JSON.parse(text) : null
  if (!response.ok) {
    const error = new Error(data?.detail || response.statusText)
    error.status = response.status
    error.data = data
    throw error
  }
  return data
}

function processQueue(error, token = null) {
  failedQueue.forEach(({ resolve, reject }) => {
    if (error) reject(error)
    else resolve(token)
  })
  failedQueue = []
}

async function doFetch(path, options = {}) {
  const { skipAuth = false, ...fetchOptions } = options

  const headers = { ...fetchOptions.headers }
  if (fetchOptions.body && !(fetchOptions.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
    if (typeof fetchOptions.body !== 'string') {
      fetchOptions.body = JSON.stringify(fetchOptions.body)
    }
  }
  if (!skipAuth && accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`
  }

  return fetch(buildUrl(path), {
    ...fetchOptions,
    headers,
    credentials: 'include',
  })
}

async function refreshAccessToken() {
  const response = await fetch(buildUrl(config.refreshEndpoint), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!response.ok) {
    throw new Error('Refresh failed')
  }
  const data = await response.json()
  if (data.access) {
    setAccessToken(data.access)
    return data.access
  }
  throw new Error('No access in refresh response')
}

async function request(path, options = {}) {
  let response
  try {
    response = await doFetch(path, options)
  } catch (err) {
    if (config.onError) config.onError(err)
    throw err
  }

  const skipRefresh =
    path.includes(config.loginEndpoint) ||
    path.includes(config.refreshEndpoint) ||
    path.includes('/logout')

  if (response.status === 401 && !options._retry && !skipRefresh) {
    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        failedQueue.push({
          resolve: (newToken) => {
            options.headers = { ...(options.headers || {}), Authorization: `Bearer ${newToken}` }
            request(path, { ...options, _retry: true }).then(resolve, reject)
          },
          reject,
        })
      })
    }

    options._retry = true
    isRefreshing = true
    try {
      const newToken = await refreshAccessToken()
      processQueue(null, newToken)
      options.headers = { ...(options.headers || {}), Authorization: `Bearer ${newToken}` }
      return request(path, options)
    } catch (refreshError) {
      processQueue(refreshError, null)
      clearAccessToken()
      if (config.onUnauthorized) config.onUnauthorized(refreshError)
      throw refreshError
    } finally {
      isRefreshing = false
    }
  }

  return parseResponse(response)
}

export const api = {
  get:    (path, options)         => request(path, { ...options, method: 'GET' }),
  post:   (path, body, options)   => request(path, { ...options, method: 'POST', body }),
  put:    (path, body, options)   => request(path, { ...options, method: 'PUT', body }),
  patch:  (path, body, options)   => request(path, { ...options, method: 'PATCH', body }),
  delete: (path, options)         => request(path, { ...options, method: 'DELETE' }),
}

export default api
