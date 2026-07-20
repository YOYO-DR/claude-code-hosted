// Helper para conocer el usuario actual via /api/v1/me/.

import { api, ApiError } from "@/lib/api";

export interface CurrentUser {
  id: number;
  username: string;
  is_verified: boolean; // TOTP confirmado
}

export async function fetchMe(): Promise<CurrentUser | null> {
  try {
    return await api<CurrentUser>("/api/v1/me/");
  } catch (err: unknown) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}