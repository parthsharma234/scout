import { Navigate } from 'react-router-dom'
import { useAuth } from './AuthProvider'

export default function ProtectedRoute({ children }) {
    const { session, loading } = useAuth()

    if (loading) {
        return <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', color: 'gray' }}>Checking authentication...</div>
    }

    if (!session) {
        return <Navigate to="/login" replace />
    }

    return children
}
