import React, { useMemo, useState } from 'react'

export default function ContactList({ contacts, selectedGuid, disabled, onSelect, onToggle }) {
  const [query, setQuery] = useState('')

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
                  </div>
                  {c.display_name && c.address && !c.is_group && (
                    <div className="addr">{c.address}</div>
                  )}
                </div>
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
            )
          })}
        </div>
      )}
    </>
  )
}
