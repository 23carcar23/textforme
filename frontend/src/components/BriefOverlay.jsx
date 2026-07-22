import React, { useEffect } from 'react'

// Shows the result of a "Brief me" run. The parent owns the async call and
// passes in one of: loading, an error string, a { summary, generated_at }
// object, or the 'no_new' sentinel.
export default function BriefOverlay({ state, onClose, onRegenerate }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const loading = state === 'loading'
  const noNew = state === 'no_new'
  const error = typeof state === 'string' && !loading && !noNew ? state : ''
  const brief = state && typeof state === 'object' ? state : null

  return (
    <div className="logs-overlay" onClick={onClose}>
      <div className="brief-sheet" onClick={(e) => e.stopPropagation()}>
        <header>
          Conversation brief
          {brief?.generated_at && (
            <span className="stamp">{formatStamp(brief.generated_at)}</span>
          )}
          <button onClick={onRegenerate} disabled={loading}>
            Regenerate
          </button>
          <button onClick={onClose}>Close (Esc)</button>
        </header>
        <div className="brief-body">
          {loading && <p className="brief-status">Summarizing recent conversations…</p>}
          {noNew && (
            <p className="brief-status">
              No new AI conversations since your last brief.
            </p>
          )}
          {error && <p className="brief-status error">{error}</p>}
          {brief && <div className="brief-text">{brief.summary}</div>}
        </div>
      </div>
    </div>
  )
}

function formatStamp(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}
