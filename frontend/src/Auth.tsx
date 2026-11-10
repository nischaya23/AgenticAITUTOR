import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ThemeToggle } from './Theme';

export type User = { id: number; email: string };
export const authExpired = 'studyroom-auth-expired';

export default function Auth({ children }: {
  children: (user: User, logout: () => Promise<void>) => ReactNode;
}) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [signup, setSignup] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);

  useEffect(() => {
    let active = true;
    async function restore() {
      const version = generation.current;
      try {
        const response = await fetch('/api/auth/me');
        if (!response.ok && response.status !== 401) throw Error('Could not connect. Refresh to try again.');
        const data = await response.json();
        if (active && version === generation.current) setUser(response.ok ? data.user : null);
      } catch {
        if (active) setError('Could not connect to the server. Refresh to try again.');
      } finally {
        if (active) setLoading(false);
      }
    }
    const expired = () => {
      generation.current++;
      setUser(null); setPassword(''); setError('Your session ended. Please log in again.');
    };
    const onVisible = () => { if (!document.hidden) void restore(); };
    window.addEventListener(authExpired, expired);
    document.addEventListener('visibilitychange', onVisible);
    void restore();
    return () => {
      active = false;
      window.removeEventListener(authExpired, expired);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    generation.current++;
    setError('');
    try {
      const response = await fetch('/api/auth/' + (signup ? 'signup' : 'login'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      const data = await response.json();
      if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Enter a valid email and a password of 8–128 characters.');
      setPassword('');
      setUser(data.user);
    } catch (failure) {
      setError((failure as Error).message);
    } finally { setPending(false); }
  }

  async function logout() {
    generation.current++;
    const response = await fetch('/api/auth/logout', { method: 'POST' });
    if (!response.ok && response.status !== 401) throw Error('Could not log out. Please try again.');
    setUser(null);
    setPassword('');
    setError('');
  }

  if (loading) return <div className="auth-shell" role="status">Loading Studyroom…</div>;
  if (user) return children(user, logout);
  return <main className="auth-shell">
    <section className="auth-card">
      <ThemeToggle />
      <p className="eyebrow">STUDYROOM</p>
      <h1>{signup ? 'Create your account' : 'Welcome back'}</h1>
      <p className="muted">Save your materials and continue learning where you left off.</p>
      {error && <p className="auth-error" role="alert">{error}</p>}
      <form onSubmit={submit}>
        <label htmlFor="auth-email">Email</label>
        <input id="auth-email" type="email" autoComplete="username" maxLength={254} required
          disabled={pending} value={email} onChange={event => setEmail(event.target.value)} />
        <label htmlFor="auth-password">Password</label>
        <input id="auth-password" type="password" autoComplete={signup ? 'new-password' : 'current-password'}
          minLength={8} maxLength={128} required disabled={pending}
          value={password} onChange={event => setPassword(event.target.value)} />
        {signup && <p className="muted">Use at least 8 characters.</p>}
        <button className="auth-submit" disabled={pending}>
          {pending ? 'Please wait…' : signup ? 'Create account' : 'Log in'}
        </button>
      </form>
      <p>{signup ? 'Already have an account?' : 'New to Studyroom?'}{' '}
        <button className="auth-switch" type="button" disabled={pending}
          onClick={() => { setSignup(!signup); setError(''); setPassword(''); }}>
          {signup ? 'Log in' : 'Sign up'}
        </button>
      </p>
    </section>
  </main>;
}
