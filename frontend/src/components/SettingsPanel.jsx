import React, { useEffect, useState } from 'react'
import { call } from '../bridge.js'

const CHOICES = {
  maximum_reply_length: ['150', '300', '600'],
  response_delay_seconds: ['0', '3', '10', '30'],
  context_message_limit: ['5', '10', '25'],
  global_rate_limit_per_hour: ['10', '20', '60'],
}

const LABELS = {
  maximum_reply_length: 'Reply length (chars)',
  response_delay_seconds: 'Response delay (s)',
  context_message_limit: 'Context messages',
  global_rate_limit_per_hour: 'Rate limit (per hour)',
}

function isTrue(v) {
  return ['1', 'true', 'yes', 'on'].includes(String(v).trim().toLowerCase())
}

function withCurrent(choices, current) {
  return choices.includes(current) ? choices : [current, ...choices]
}

export default function SettingsPanel({ settings, hasApiKey, disabled, onChange, onSaveApiKey }) {
  const [models, setModels] = useState(null)
  const [quietDraft, setQuietDraft] = useState(null) // null = not editing
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
  const quietValue =
    settings.quiet_hours_start && settings.quiet_hours_end
      ? `${settings.quiet_hours_start}-${settings.quiet_hours_end}`
      : ''

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

  function commitQuietHours() {
    const raw = (quietDraft ?? '').trim()
    setQuietDraft(null)
    if (raw === quietValue) return
    const match = raw === '' ? ['', '', ''] : raw.match(/^(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$/)
    if (!match) return
    onChange('quiet_hours_start', raw === '' ? '' : match[1])
    onChange('quiet_hours_end', raw === '' ? '' : match[2])
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
            {!models.some((m) => m.id === settings.selected_model_id) && (
              <option value={settings.selected_model_id ?? ''}>
                {settings.selected_model_id || '(not set)'}
              </option>
            )}
            {models.map((m) => (
              <option key={m.id} value={m.id}>{m.display_name || m.id}</option>
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

      <div className="setting-row">
        <label htmlFor="quiet-hours">Quiet hours</label>
        <input
          id="quiet-hours"
          type="text"
          placeholder="22:00-08:00 or blank"
          value={quietDraft ?? quietValue}
          disabled={disabled}
          onChange={(e) => setQuietDraft(e.target.value)}
          onBlur={commitQuietHours}
          onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
        />
      </div>
    </div>
  )
}
