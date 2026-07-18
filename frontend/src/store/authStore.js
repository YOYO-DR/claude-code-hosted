import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * Auth store. Solo persiste el access token y el user.
 * El refresh token NO se persiste aquí — vive en cookie HttpOnly.
 */
export const useAuthStore = create()(
  persist(
    (set) => ({
      user: null,
      access: null,
      isAuthenticated: false,

      login: (userData, accessToken) =>
        set({
          user: userData,
          access: accessToken,
          isAuthenticated: true,
        }),

      logout: () =>
        set({
          user: null,
          access: null,
          isAuthenticated: false,
        }),

      setAccess: (accessToken) => set({ access: accessToken }),

      setUser: (userData) => set({ user: userData }),
    }),
    {
      name: 'auth-settings',
      partialize: (state) => ({
        user: state.user,
        access: state.access,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
)
