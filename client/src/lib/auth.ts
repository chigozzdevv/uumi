import { getApp, getApps, initializeApp } from "firebase/app"
import {
  GoogleAuthProvider,
  getAuth,
  onAuthStateChanged,
  signInWithPopup,
  signOut,
  type Auth,
  type User,
} from "firebase/auth"

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
}

export const authenticationConfigured = Object.values(firebaseConfig).every(Boolean)

let auth: Auth | null = null

function configuredAuth(): Auth {
  if (!authenticationConfigured) {
    throw new Error("Firebase authentication is not configured")
  }
  if (!auth) {
    const app = getApps().length > 0 ? getApp() : initializeApp(firebaseConfig)
    auth = getAuth(app)
  }
  return auth
}

export function observeIdentity(listener: (user: User | null) => void): () => void {
  if (!authenticationConfigured) {
    listener(null)
    return () => undefined
  }
  return onAuthStateChanged(configuredAuth(), listener)
}

export async function signInWithGoogle(): Promise<void> {
  const provider = new GoogleAuthProvider()
  provider.setCustomParameters({ prompt: "select_account" })
  await signInWithPopup(configuredAuth(), provider)
}

export async function identityToken(forceRefresh = false): Promise<string> {
  const user = configuredAuth().currentUser
  if (!user) throw new Error("Authentication required")
  return user.getIdToken(forceRefresh)
}

export async function signOutIdentity(): Promise<void> {
  if (!authenticationConfigured) return
  await signOut(configuredAuth())
}
