import { useState } from 'react'
import { login } from '@/api/authService'

export default function LoginPage({ onLogin }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const data = await login(email, password)
      onLogin?.(data.user)
    } catch (err) {
      setError(
        err.status === 401
          ? 'Credenciales inválidas'
          : err.status === 400
            ? 'Faltan datos (email y password requeridos)'
            : `Error ${err.status || ''}: ${err.message}`
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <main style={{ maxWidth: 360, margin: '64px auto', padding: 24 }}>
      <h1>Login</h1>
      <p style={{ color: '#666' }}>
        Prueba de CORS + DRF SimpleJWT. Inicia sesión con un superusuario.
      </p>
      <form onSubmit={handleSubmit}>
        <label style={{ display: 'block', marginTop: 12 }}>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="username"
            style={{ width: '100%', padding: 8, marginTop: 4 }}
          />
        </label>
        <label style={{ display: 'block', marginTop: 12 }}>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
            style={{ width: '100%', padding: 8, marginTop: 4 }}
          />
        </label>
        {error && (
          <p style={{ color: 'crimson', marginTop: 12 }}>{error}</p>
        )}
        <button
          type="submit"
          disabled={loading}
          style={{ marginTop: 16, padding: '8px 16px', width: '100%' }}
        >
          {loading ? 'Entrando…' : 'Entrar'}
        </button>
      </form>
    </main>
  )
}
