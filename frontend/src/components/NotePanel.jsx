import React, { useEffect, useState } from 'react'

const MAX_NOTE = 2000

export default function NotePanel({ contact, disabled, onSave }) {
  const [draft, setDraft] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setDraft(contact?.description ?? '')
    setSaved(false)
  }, [contact?.chat_guid])

  if (!contact) {
    return <div className="target" style={{ padding: '0 20px' }}>Select a contact to add a note.</div>
  }
  const name = contact.display_name || contact.address || '(unknown)'
  if (contact.is_group) {
    return <div className="target" style={{ padding: '0 20px' }}>{name} — group chats cannot have notes.</div>
  }

  const dirty = draft.trim() !== (contact.description ?? '').trim()

  async function save() {
    const ok = await onSave(contact.chat_guid, draft.trim())
    if (ok) setSaved(true)
  }

  return (
    <>
      <div className="target">Note for {name}:</div>
      <textarea
        value={draft}
        maxLength={MAX_NOTE}
        disabled={disabled}
        placeholder={'Describe this contact and how you want replies to them to sound. '
          + 'e.g. "This is my very strict mom — always be warm, polite, and a little formal. '
          + 'Never joke about money. If she asks when I\'m visiting, say I\'ll call her later."'}
        onChange={(e) => {
          setDraft(e.target.value)
          setSaved(false)
        }}
        onBlur={() => dirty && save()}
      />
      <div className="hint">
        {saved && !dirty ? (
          <span className="saved">Saved — this note is added to the AI prompt for {name}.</span>
        ) : (
          `${draft.length}/${MAX_NOTE} — click Save or click away to save. Added to the AI prompt for this contact.`
        )}
      </div>
      <div className="prompt-actions" style={{ padding: '4px 20px 0' }}>
        <button className="btn" disabled={disabled || !dirty} onClick={save}>Save note</button>
      </div>
    </>
  )
}
