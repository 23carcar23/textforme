import React, { useEffect, useState } from 'react'
import { call } from '../bridge.js'

const MAX_PROMPT = 6000

// key: settings key, mono: render as monospace (the system prompt)
const FIELDS = [
  {
    key: 'persona_prompt',
    label: 'About me',
    desc: 'Describe yourself so replies sound like you — who you are, your voice, '
      + 'anything the AI should know to stand in for you.',
    placeholder: "e.g. I'm Carson, 28, an easygoing software engineer in Denver. I'm into "
      + 'climbing and bad puns. I keep things upbeat and never rude.',
    mono: false,
  },
  {
    key: 'style_profile',
    label: 'My texting style',
    desc: 'How you actually type — casing, punctuation, emoji, length, favorite phrases.',
    placeholder: "e.g. all lowercase, almost no punctuation, short replies, lots of \"haha\" "
      + 'and "lol", occasional 🙏. never more than a sentence or two.',
    mono: false,
  },
  {
    key: 'system_prompt',
    label: 'System prompt (advanced)',
    desc: 'The base instructions sent to the AI for every reply. Leave blank to use the '
      + 'built-in default (shown below). Use {contact_name} and {max_chars} as placeholders.',
    placeholder: '',
    mono: true,
  },
]

function PromptField({ field, value, defaultText, disabled, onSave }) {
  const [draft, setDraft] = useState(value)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setDraft(value)
    setSaved(false)
  }, [value])

  const dirty = draft !== value

  async function save() {
    const ok = await onSave(field.key, draft)
    if (ok) setSaved(true)
  }

  const isSystem = field.key === 'system_prompt'
  const effectivePlaceholder =
    isSystem && defaultText ? defaultText : field.placeholder

  return (
    <div className="prompt-field">
      <div className="label">{field.label}</div>
      <div className="desc">{field.desc}</div>
      <textarea
        className={field.mono ? 'mono' : ''}
        value={draft}
        maxLength={MAX_PROMPT}
        disabled={disabled}
        placeholder={effectivePlaceholder}
        onChange={(e) => {
          setDraft(e.target.value)
          setSaved(false)
        }}
      />
      <div className="prompt-actions">
        <button className="btn" disabled={disabled || !dirty} onClick={save}>Save</button>
        {isSystem && draft !== '' && (
          <button className="btn ghost" disabled={disabled} onClick={() => setDraft('')}>
            Restore default
          </button>
        )}
        {isSystem && draft === '' && defaultText && (
          <button className="btn ghost" disabled={disabled} onClick={() => setDraft(defaultText)}>
            Load default to edit
          </button>
        )}
        {saved && !dirty && <span className="saved">Saved</span>}
        <span style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: 12 }}>
          {draft.length}/{MAX_PROMPT}
        </span>
      </div>
    </div>
  )
}

export default function PromptsOverlay({ disabled, onClose, onSaved }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    call('get_prompts').then((res) => res.ok && setData(res))
  }, [])

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function save(key, value) {
    const res = await call('set_setting', key, value)
    if (res.ok) {
      setData((prev) => ({ ...prev, [key]: value }))
      onSaved?.()
      return true
    }
    return false
  }

  return (
    <div className="logs-overlay" onClick={onClose}>
      <div className="prompts-sheet" onClick={(e) => e.stopPropagation()}>
        <header>
          Prompts
          <button onClick={onClose}>Close (Esc)</button>
        </header>
        <div className="prompts-body">
          {data === null ? (
            <div className="empty">Loading…</div>
          ) : (
            FIELDS.map((f) => (
              <PromptField
                key={f.key}
                field={f}
                value={data[f.key] ?? ''}
                defaultText={data.system_prompt_default ?? ''}
                disabled={disabled}
                onSave={save}
              />
            ))
          )}
        </div>
      </div>
    </div>
  )
}
