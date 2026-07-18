import { useState, useEffect } from 'react'
import LoginPage from '@/pages/LoginPage'
import { useAuthStore } from '@/store/authStore'
import { getMe, logout } from '@/api/authService'
import './App.css'

function App() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const user = useAuthStore((s) => s.user)
  const [me, setMe] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!isAuthenticated) return
    getMe()
      .then((data) => setMe(data))
      .catch((err) => setError(err.message))
  }, [isAuthenticated])

  if (!isAuthenticated) return <LoginPage />

  async function handleLogout() {
    await logout()
  }

  return (
    <main style={{ padding: 24 }}>
      <h1>CORS OK — SimpleJWT funcionando</h1>
      <p>
        Bienvenido <strong>{user?.email ?? '...'}</strong>
      </p>
      {error && <p style={{ color: 'crimson' }}>Error: {error}</p>}
      <details>
        <summary>Datos de /api/users/me/ (válida CORS + Authorization)</summary>
        <pre>{JSON.stringify(me, null, 2)}</pre>
      </details>
      <button onClick={handleLogout} style={{ marginTop: 16 }}>
        Cerrar sesión
      </button>
    </main>
  )
}

export default App
