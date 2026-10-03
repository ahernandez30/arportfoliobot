import { describe, expect, it } from 'vitest'
import { validateLogin } from './login'

describe('validateLogin', () => {
  it('requires both fields', () => {
    expect(validateLogin({ email: '', password: 'x' })).toMatch(/Enter/)
    expect(validateLogin({ email: 'a@b.co', password: '' })).toMatch(/Enter/)
  })

  it('rejects a malformed email', () => {
    expect(validateLogin({ email: 'rafa', password: 'secret' })).toMatch(/does not look right/)
  })

  it('accepts a normal email, ignoring surrounding spaces', () => {
    expect(validateLogin({ email: '  rafa@example.com ', password: 'secret' })).toBeNull()
  })
})
