import { useState } from 'react'
import { useAuth } from '../components/AuthProvider'
import { useUserProfile, useUserBookmarks } from '../hooks/useApi'
import Header from '../components/Header'
import './ProfilePage.css'

const MAJOR_CITIES = [
    'San Francisco', 'New York', 'London', 'Berlin', 'Tel Aviv', 'Singapore', 'Tokyo', 'Palo Alto', 'Austin', 'Seattle'
]

export default function ProfilePage() {
    const { user } = useAuth()
    const { profile, loading: profileLoading, updateProfile, refetch: refetchProfile } = useUserProfile(user?.id)
    const { bookmarks, loading: bookmarksLoading } = useUserBookmarks(user?.id)

    const [activeTab, setActiveTab] = useState('info')
    const [editData, setEditData] = useState(null)
    const [saving, setSaving] = useState(false)

    // Sync editData with profile when profile loads
    if (profile && !editData && !saving) {
        setEditData({
            niche: profile.niche || '',
            bio: profile.bio || '',
            firm: profile.firm || '',
            location: profile.location || '',
            avatar_url: profile.avatar_url || '',
        })
    }

    async function handleSave(e) {
        e.preventDefault()
        setSaving(true)
        try {
            await updateProfile(editData)
            await refetchProfile()
        } catch (err) {
            alert('Failed to update profile: ' + err.message)
        } finally {
            setSaving(false)
        }
    }

    return (
        <div className="profile-root">
            <Header showBackBtn={true} />
            <main className="profile-container">
                <header className="profile-header">
                    <div className="profile-summary">
                        <div className="profile-avatar-large">
                            {editData?.avatar_url ? (
                                <img src={editData.avatar_url} alt="Avatar" />
                            ) : (
                                <div className="avatar-placeholder">{user?.email?.[0].toUpperCase()}</div>
                            )}
                        </div>
                        <div className="profile-titles">
                            <h1>{user?.email}</h1>
                            <p className="profile-sub">{editData?.firm || 'VC Partner'} • {editData?.location || 'Global'}</p>
                        </div>
                    </div>

                    <nav className="profile-tabs">
                        <button
                            className={`tab-btn ${activeTab === 'info' ? 'active' : ''}`}
                            onClick={() => setActiveTab('info')}
                        >
                            Profile Info
                        </button>
                        <button
                            className={`tab-btn ${activeTab === 'saved' ? 'active' : ''}`}
                            onClick={() => setActiveTab('saved')}
                        >
                            Saved Startups ({bookmarks?.length || 0})
                        </button>
                    </nav>
                </header>

                <section className="profile-content">
                    {activeTab === 'info' && (
                        <form className="profile-form" onSubmit={handleSave}>
                            <div className="form-group">
                                <label>Niche / Investment Focus</label>
                                <input
                                    type="text"
                                    placeholder="e.g. Early-stage Fintech, Generative AI"
                                    value={editData?.niche || ''}
                                    onChange={e => setEditData({ ...editData, niche: e.target.value })}
                                />
                            </div>

                            <div className="form-group">
                                <label>Firm / Organization</label>
                                <input
                                    type="text"
                                    placeholder="e.g. Sequoia Capital"
                                    value={editData?.firm || ''}
                                    onChange={e => setEditData({ ...editData, firm: e.target.value })}
                                />
                            </div>

                            <div className="form-group">
                                <label>Location</label>
                                <select
                                    value={editData?.location || ''}
                                    onChange={e => setEditData({ ...editData, location: e.target.value })}
                                >
                                    <option value="">Select a city</option>
                                    {MAJOR_CITIES.map(city => (
                                        <option key={city} value={city}>{city}</option>
                                    ))}
                                </select>
                            </div>

                            <div className="form-group">
                                <label>Avatar</label>
                                <div className="avatar-upload-wrap">
                                    <button
                                        type="button"
                                        className="upload-trigger-btn"
                                        onClick={() => document.getElementById('avatar-input').click()}
                                    >
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                            <polyline points="17 8 12 3 7 8" />
                                            <line x1="12" y1="3" x2="12" y2="15" />
                                        </svg>
                                        {editData?.avatar_url ? 'Change Photo' : 'Upload Photo'}
                                    </button>
                                    <input
                                        id="avatar-input"
                                        type="file"
                                        accept="image/*"
                                        style={{ display: 'none' }}
                                        onChange={(e) => {
                                            const file = e.target.files[0]
                                            if (!file) return
                                            const reader = new FileReader()
                                            reader.onload = (event) => {
                                                setEditData({ ...editData, avatar_url: event.target.result })
                                            }
                                            reader.readAsDataURL(file)
                                        }}
                                    />
                                    {editData?.avatar_url && (
                                        <button
                                            type="button"
                                            className="remove-avatar-btn"
                                            onClick={() => setEditData({ ...editData, avatar_url: '' })}
                                        >
                                            Remove
                                        </button>
                                    )}
                                </div>
                            </div>

                            <div className="form-group">
                                <label>Bio / Description</label>
                                <textarea
                                    rows="4"
                                    placeholder="Tell us about yourself..."
                                    value={editData?.bio || ''}
                                    onChange={e => setEditData({ ...editData, bio: e.target.value })}
                                />
                            </div>

                            <button type="submit" className="save-btn" disabled={saving}>
                                {saving ? 'Saving...' : 'Save Profile'}
                            </button>
                        </form>
                    )}

                    {activeTab === 'saved' && (
                        <div className="bookmarks-list">
                            {bookmarks?.length === 0 ? (
                                <p className="empty-msg">No bookmarked startups yet. Go to the dashboard to find some!</p>
                            ) : (
                                bookmarks.map(item => (
                                    <div key={item.entity_key} className="bookmark-item">
                                        <div className="bookmark-info">
                                            <div className="bookmark-main">
                                                <span className="bookmark-key">{item.display_name || item.entity_key}</span>
                                                <span className="bookmark-score mono">{Number(item.trend_score).toFixed(1)}</span>
                                            </div>
                                            {item.top_keywords && item.top_keywords.length > 0 && (
                                                <div className="bookmark-tags">
                                                    {item.top_keywords.slice(0, 3).map(kw => (
                                                        <span key={kw} className="bookmark-tag">{kw}</span>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                        <div className="bookmark-actions">
                                            <button className="draft-email-btn" onClick={() => alert('Email drafting integration coming soon!')}>
                                                Draft Email
                                            </button>
                                            <a href={`/dashboard?entity_key=${item.entity_key}`} className="view-link">
                                                View in Dashboard
                                            </a>
                                        </div>
                                    </div>
                                ))
                            )}
                        </div>
                    )}
                </section>
            </main>
        </div>
    )
}
