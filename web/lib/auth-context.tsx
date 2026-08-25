"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  onAuthStateChanged,
  signInWithPopup,
  signOut as firebaseSignOut,
} from "firebase/auth";
import { auth, googleProvider } from "./firebase";

type UserRole = "admin" | "user";

type AuthUser = {
  uid: string;
  email: string;
  displayName: string;
  photoURL: string | null;
  role: UserRole;
  idToken: string;
};

type AuthContextValue = {
  user: AuthUser | null;
  loading: boolean;
  signInWithGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
  isAdmin: boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  // Keep the server and first browser render identical. `auth` is deliberately
  // null during SSR, but exists in the browser; deriving initial state from it
  // causes a hydration mismatch before Firebase finishes restoring the session.
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const authInstance = auth;
    // Effects only execute in the browser, where firebase.ts constructs Auth.
    // Keep the guard for defensive type narrowing without introducing a
    // synchronous state update that can cause a cascading render.
    if (!authInstance) return;
    const unsubscribe = onAuthStateChanged(authInstance, async (firebaseUser) => {
      if (firebaseUser) {
        const idToken = await firebaseUser.getIdToken();
        const role = await fetchUserRole(firebaseUser.uid, idToken);
        setUser({
          uid: firebaseUser.uid,
          email: firebaseUser.email || "",
          displayName: firebaseUser.displayName || "",
          photoURL: firebaseUser.photoURL,
          role,
          idToken,
        });
      } else {
        setUser(null);
      }
      setLoading(false);
    });
    return () => unsubscribe();
  }, []);

  const signInWithGoogle = useCallback(async () => {
    if (!auth || !googleProvider) throw new Error("Firebase Auth is only available in the browser");
    await signInWithPopup(auth, googleProvider);
  }, []);

  const signOut = useCallback(async () => {
    if (!auth) return;
    await firebaseSignOut(auth);
    setUser(null);
  }, []);

  const isAdmin = user?.role === "admin";

  const value = useMemo(
    () => ({ user, loading, signInWithGoogle, signOut, isAdmin }),
    [user, loading, signInWithGoogle, signOut, isAdmin]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

async function fetchUserRole(uid: string, idToken: string): Promise<UserRole> {
  try {
    const res = await fetch(`${API_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${idToken}` },
    });
    if (res.ok) {
      const data = await res.json();
      return data.role || "user";
    }
    if (res.status === 404) {
      const createRes = await fetch(`${API_URL}/api/v1/auth/register`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${idToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ uid }),
      });
      if (createRes.ok) {
        const data = await createRes.json();
        return data.role || "user";
      }
    }
    return "user";
  } catch {
    return "user";
  }
}
