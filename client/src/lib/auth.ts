import { getApp, getApps, initializeApp } from "firebase/app"
import {
  createUserWithEmailAndPassword,
  GoogleAuthProvider,
  getAuth,
  onAuthStateChanged,
  sendEmailVerification,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
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

export class EmailVerificationRequiredError extends Error {
  constructor() {
    super("Verify your email before signing in")
    this.name = "EmailVerificationRequiredError"
  }
}

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

export async function signInWithEmail(email: string, password: string): Promise<void> {
  const configured = configuredAuth()
  const credential = await signInWithEmailAndPassword(configured, email.trim(), password)
  if (!credential.user.emailVerified) {
    await signOut(configured)
    throw new EmailVerificationRequiredError()
  }
}

export async function createEmailAccount(email: string, password: string): Promise<void> {
  const configured = configuredAuth()
  const credential = await createUserWithEmailAndPassword(configured, email.trim(), password)
  await sendEmailVerification(credential.user)
  await signOut(configured)
}

export async function resetEmailPassword(email: string): Promise<void> {
  await sendPasswordResetEmail(configuredAuth(), email.trim())
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
