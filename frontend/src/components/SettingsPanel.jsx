import React, { useEffect, useState } from 'react'
import { call } from '../bridge.js'

const CHOICES = {
  context_message_limit: ['5', '10', '25', '50', 'unlimited'],
}

const LABELS = {
  context_message_limit: 'Context messages',
}

function isTrue(v) {
  return ['1', 'true', 'yes', 'on'].includes(String(v).trim().toLowerCase())
}

function withCurrent(choices, current) {
  return choices.includes(current) ? choices : [current, ...choices]
}

export default function SettingsPanel({ settings, hasApiKey, disabled, onChange, onSaveApiKey }) {
  const [models, setModels] = useState(null)
  const [keyEditing, setKeyEditing] = useState(false)
  const [keyDraft, setKeyDraft] = useState('')
  const [keyError, setKeyError] = useState('')

  useEffect(() => {
    if (disabled || !hasApiKey) return
    let alive = true
    call('list_models').then((res) => {
      if (alive && res.ok) setModels(res.models ?? [])
    })
    return () => { alive = false }
  }, [disabled, hasApiKey])

  const aiOn = isTrue(settings.global_ai_enabled)

  async function submitKey() {
    const key = keyDraft.trim()
    if (!key) return
    const res = await onSaveApiKey(key)
    if (res?.ok) {
      setKeyEditing(false)
      setKeyDraft('')
      setKeyError('')
    } else {
      setKeyError(
        res?.error === 'BAD_KEY'
          ? 'That does not look like an Anthropic key (starts with sk-ant-).'
          : 'Could not save the key to the Keychain.'
      )
    }
  }

  return (
    <div>
      <div className="setting-row">
        <label htmlFor="ai-switch">AI service</label>
        <button
          id="ai-switch"
          className={`switch ${aiOn ? 'on' : ''}`}
          role="switch"
          aria-checked={aiOn}
          disabled={disabled}
          onClick={() => onChange('global_ai_enabled', aiOn ? 'false' : 'true')}
        />
      </div>

      <div className="setting-row">
        <label htmlFor="model-select">Anthropic model</label>
        {models?.length ? (
          <select
            id="model-select"
            value={settings.selected_model_id ?? ''}
            disabled={disabled}
            onChange={(e) => onChange('selected_model_id', e.target.value)}
          >
            {!models.some((m) => m.model_id === settings.selected_model_id) && (
              <option value={settings.selected_model_id ?? ''}>
                {settings.selected_model_id || '(not set)'}
              </option>
            )}
            {models.map((m) => (
              <option key={m.model_id} value={m.model_id}>{m.display_name || m.model_id}</option>
            ))}
          </select>
        ) : (
          <span className="value">{settings.selected_model_id || '(not set)'}</span>
        )}
      </div>

      <div className="setting-row">
        <label>API key</label>
        {keyEditing ? (
          <div className="apikey-row">
            <input
              type="password"
              value={keyDraft}
              placeholder="sk-ant-…"
              autoFocus
              disabled={disabled}
              onChange={(e) => {
                setKeyDraft(e.target.value)
                setKeyError('')
              }}
              onKeyDown={(e) => e.key === 'Enter' && submitKey()}
            />
            <button className="btn" disabled={disabled || !keyDraft.trim()} onClick={submitKey}>
              Save
            </button>
            <button
              className="link-btn"
              onClick={() => {
                setKeyEditing(false)
                setKeyDraft('')
                setKeyError('')
              }}
            >
              Cancel
            </button>
          </div>
        ) : (
          <div className="apikey-row">
            <span className="value" style={{ color: hasApiKey ? 'var(--green)' : 'var(--amber)' }}>
              {hasApiKey ? 'configured' : 'missing'}
            </span>
            <button className="link-btn" disabled={disabled} onClick={() => setKeyEditing(true)}>
              {hasApiKey ? 'Replace' : 'Add key'}
            </button>
          </div>
        )}
      </div>
      {keyError && (
        <div className="setting-row" style={{ paddingTop: 0 }}>
          <span style={{ color: 'var(--amber)', fontSize: 12.5 }}>{keyError}</span>
        </div>
      )}

      {Object.keys(CHOICES).map((key) => (
        <div className="setting-row" key={key}>
          <label htmlFor={`sel-${key}`}>{LABELS[key]}</label>
          <select
            id={`sel-${key}`}
            value={String(settings[key] ?? '')}
            disabled={disabled}
            onChange={(e) => onChange(key, e.target.value)}
          >
            {withCurrent(CHOICES[key], String(settings[key] ?? '')).map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        </div>
      ))}
    </div>
  )
}
