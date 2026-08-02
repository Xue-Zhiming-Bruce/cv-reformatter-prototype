import React, { createContext, useCallback, useContext, useEffect, useState } from "react"
import type { UserOut } from "../types"

interface AuthContextValue {
  user: UserOut | null
  isAuthLoading: boolean
  signup: (email: string, password: string) => Promise<UserOut>
  login: (email: string, password: string) => Promise<UserOut>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null)
  const [isAuthLoading, setIsAuthLoading] = useState(true)

  useEffect(() => {
    fetch("/api/auth/me", { credentials: "include" })
      .then(res => (res.ok ? res.json() : null))
      .then(data => setUser(data as UserOut | null))
      .catch(() => setUser(null))
      .finally(() => setIsAuthLoading(false))
  }, [])

  const signup = useCallback(async (email: string, password: string): Promise<UserOut> => {
    const res = await fetch("/api/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ email, password }),
    })
    const body = await res.json() as { detail?: string } & Partial<UserOut>
    if (!res.ok) throw new Error(body.detail ?? "Signup failed.")
    setUser(body as UserOut)
    return body as UserOut
  }, [])

  const login = useCallback(async (email: string, password: string): Promise<UserOut> => {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ email, password }),
    })
    const body = await res.json() as { detail?: string } & Partial<UserOut>
    if (!res.ok) throw new Error(body.detail ?? "Login failed.")
    setUser(body as UserOut)
    return body as UserOut
  }, [])

  const logout = useCallback(async (): Promise<void> => {
    await fetch("/api/auth/logout", { method: "POST", credentials: "include" })
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, isAuthLoading, signup, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
