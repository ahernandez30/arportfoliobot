export type LoginFields = { email: string; password: string }

/** Returns an error message for the form, or null when it can be submitted. */
export function validateLogin({ email, password }: LoginFields): string | null {
  const trimmed = email.trim()
  if (!trimmed || !password) return 'Enter your email and password.'
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) return 'That email address does not look right.'
  return null
}
