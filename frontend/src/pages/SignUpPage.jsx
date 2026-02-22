import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../components/AuthProvider'
import './SignUpPage.css'

export default function SignUpPage() {
    const navigate = useNavigate()
    const { session } = useAuth()
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState(null)
    const [success, setSuccess] = useState(false)

    useEffect(() => {
        if (session) {
            navigate('/dashboard', { replace: true })
        }
    }, [session, navigate])

    const handleSignUp = async (e) => {
        e.preventDefault()
        setLoading(true)
        setError(null)

        const { error } = await supabase.auth.signUp({
            email,
            password,
        })

        if (error) {
            setError(error.message)
        } else {
            setSuccess(true)
        }
        setLoading(false)
    }

    return (
        <div className="auth-root">
            <div className="auth-card">
                <div className="auth-brand-wrap">
                    <div className="auth-brand-icon" />
                    <div className="auth-brand">Scout</div>
                </div>
                <h1 className="auth-title">Create Account</h1>
                <p className="auth-subtitle">Join the exclusive VC partner platform.</p>

                {error && <div className="auth-error">{error}</div>}
                {success && (
                    <div className="auth-success">
                        Registration successful! Please check your email for a confirmation link, or log in if email confirmation is disabled.
                    </div>
                )}

                <form className="auth-form" onSubmit={handleSignUp}>
                    <div className="auth-field">
                        <label htmlFor="email">Email</label>
                        <input
                            id="email"
                            type="email"
                            placeholder="you@company.com"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            required
                        />
                    </div>
                    <div className="auth-field">
                        <label htmlFor="password">Password</label>
                        <input
                            id="password"
                            type="password"
                            placeholder="••••••••"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            required
                            minLength={6}
                        />
                    </div>
                    <button type="submit" className="auth-button" disabled={loading || success}>
                        {loading ? 'Signing Up...' : 'Sign Up'}
                    </button>
                </form>

                <div className="auth-footer">
                    Already have an account? <Link to="/login">Log in</Link>
                </div>
            </div>
        </div>
    )
}
