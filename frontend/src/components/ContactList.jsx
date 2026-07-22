import React, { useEffect, useMemo, useRef, useState } from 'react'

function formatRemaining(seconds) {
  const s = Math.max(0, Math.round(seconds))
  const m = Math.floor(s / 60)
  const rem = s % 60
  return `${m}:${String(rem).padStart(2, '0')}`
}

export default function ContactList({ contacts, selectedGuid, disabled, onSelect, onToggle, onToggleTimer }) {
  const [query, setQuery] = useState('')

  // Smoothly tick each contact's countdown between the 3s server polls. The
  // server value (reply_timer_remaining) re-seeds the local clock whenever it
  // changes; a 1s interval decrements the local copy in between.
  const [localRemaining, setLocalRemaining] = useState({})
  const lastSeenRef = useRef({})

  useEffect(() => {
    const next = { ...localRemaining }
    const seen = {}
    for (const c of contacts) {
      const server = c.reply_timer_remaining
      seen[c.chat_guid] = server
      // Re-seed only when the server reports a new value (or a first sighting).
      if (server != null && server !== lastSeenRef.current[c.chat_guid]) {
        next[c.chat_guid] = server
      }
      if (server == null) delete next[c.chat_guid]
    }
    // Drop timers for contacts no longer present.
    for (const guid of Object.keys(next)) {
      if (!(guid in seen)) delete next[guid]
    }
    lastSeenRef.current = seen
    setLocalRemaining(next)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contacts])

  useEffect(() => {
    const timer = setInterval(() => {
      setLocalRemaining((prev) => {
        const keys = Object.keys(prev)
        if (keys.length === 0) return prev
        const next = {}
        for (const guid of keys) next[guid] = Math.max(0, prev[guid] - 1)
        return next
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return contacts
    return contacts.filter((c) => {
      const name = (c.display_name || '').toLowerCase()
      const addr = (c.address || '').toLowerCase()
      return name.includes(q) || addr.includes(q)
    })
  }, [contacts, query])

  return (
    <>
      <div className="search-wrap">
        <input
          type="text"
          value={query}
          placeholder="Search contacts…"
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search contacts"
        />
      </div>
      {contacts.length === 0 ? (
        <div className="empty">No conversations found yet.</div>
      ) : filtered.length === 0 ? (
        <div className="empty">No contacts match “{query}”.</div>
      ) : (
        <div className="contact-list">
          {filtered.map((c) => {
            const name = c.display_name || c.address || '(unknown)'
            const remaining = localRemaining[c.chat_guid]
            const timerActive = remaining != null
            return (
              <div
                key={c.chat_guid}
                role="button"
                tabIndex={0}
                className={`contact-row ${c.chat_guid === selectedGuid ? 'selected' : ''}`}
                onClick={() => onSelect(c.chat_guid)}
                onKeyDown={(e) => e.key === 'Enter' && onSelect(c.chat_guid)}
              >
                <div className="who">
                  <div className="name">
                    {name}
                    {c.is_group && <span className="group-tag">group</span>}
                    {timerActive && (
                      <span
                        className="timer-badge"
                        title="Replying in one batch when this countdown ends"
                      >
                        ⏱ {formatRemaining(remaining)}
                      </span>
                    )}
                  </div>
                  {c.display_name && c.address && !c.is_group && (
                    <div className="addr">{c.address}</div>
                  )}
                </div>
                <div className="contact-actions">
                  {!c.is_group && (
                    <button
                      className={`timer-toggle ${c.reply_timer_enabled ? 'on' : ''}`}
                      disabled={disabled}
                      onClick={(e) => {
                        e.stopPropagation()
                        onToggleTimer(c)
                      }}
                      aria-label={`Reply timer for ${name}`}
                      aria-pressed={Boolean(c.reply_timer_enabled)}
                      title="Realistic texting: batch a burst behind a random 0–3 min timer"
                    >
                      {c.reply_timer_enabled ? 'Timer on' : 'Timer off'}
                    </button>
                  )}
                  <button
                    className={`bubble-toggle ${c.ai_enabled ? 'on' : ''}`}
                    disabled={disabled || c.is_group}
                    onClick={(e) => {
                      e.stopPropagation()
                      onToggle(c)
                    }}
                    aria-label={`AI replies for ${name}`}
                    aria-pressed={Boolean(c.ai_enabled)}
                  >
                    {c.ai_enabled ? 'AI on' : 'AI off'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}
