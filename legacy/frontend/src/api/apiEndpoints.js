const API_ENDPOINTS = {
  auth: {
    login: '/api/auth/token',
    refresh: '/api/auth/token/refresh',
    logout: '/api/auth/logout',
  },
  user: {
    me: '/api/users/me',
  },
}

export default API_ENDPOINTS
