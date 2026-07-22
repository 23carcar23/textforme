import React, { useCallback, useEffect, useState } from 'react'
import { call } from './bridge.js'
import ContactList from './components/ContactList.jsx'
import SettingsPanel from './components/SettingsPanel.jsx'
import NotePanel from './components/NotePanel.jsx'
import LogsOverlay from './components/LogsOverlay.jsx'
import PromptsOverlay from './components/PromptsOverlay.jsx'
import BriefOverlay from './components/BriefOverlay.jsx'

const POLL_MS = 3000

export default function App() {
  const [state, setState] = useState(null) // null until first get_state resolves
  const [selectedGuid, setSelectedGuid] = useState(null)
  const [showLogs, setShowLogs] = useState(false)
  const [showPrompts, setShowPrompts] = useState(false)
  const [brief, setBrief] = useState(null) // null hidden | 'loading' | 'no_new' | error string | {summary, generated_at}
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    const next = await call('get_state')
    if (next?.ok) setState(next)
    else if (next) setState((prev) => (prev ? { ...prev, connected: false } : next))
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, POLL_MS)
    return () => clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    if (!error) return
    const t = setTimeout(() => setError(''), 4000)
    return () => clearTimeout(t)
  }, [error])

  const connected = Boolean(state?.connected)
  const contacts = state?.contacts ?? []
  const selected = contacts.find((c) => c.chat_guid === selectedGuid) ?? null

  async function toggleAi(contact) {
    const enabled = !contact.ai_enabled
    // optimistic flip; refresh() reconciles
    setState((prev) => ({
      ...prev,
      contacts: prev.contacts.map((c) =>
        c.chat_guid === contact.chat_guid ? { ...c, ai_enabled: enabled } : c
      ),
    }))
    const res = await call('set_ai', contact.chat_guid, enabled)
    if (!res.ok) {
      setError(
        res.error === 'GROUP_FORBIDDEN'
          ? 'Group chats cannot have AI enabled.'
          : `Could not update contact (${res.error}).`
      )
    }
    refresh()
  }

  async function toggleTimer(contact) {
    const enabled = !contact.reply_timer_enabled
    // optimistic flip; refresh() reconciles
    setState((prev) => ({
      ...prev,
      contacts: prev.contacts.map((c) =>
        c.chat_guid === contact.chat_guid ? { ...c, reply_timer_enabled: enabled } : c
      ),
    }))
    const res = await call('set_reply_timer', contact.chat_guid, enabled)
    if (!res.ok) {
      setError(
        res.error === 'GROUP_FORBIDDEN'
          ? 'Group chats cannot use a reply timer.'
          : `Could not update reply timer (${res.error}).`
      )
    }
    refresh()
  }

  async function saveNote(chatGuid, description) {
    const res = await call('set_note', chatGuid, description)
    if (!res.ok) {
      setError(`Could not save note (${res.error}).`)
      return false
    }
    refresh()
    return true
  }

  async function setSetting(key, value) {
    const res = await call('set_setting', key, value)
    if (!res.ok) setError(`Could not save setting (${res.error}).`)
    refresh()
  }

  async function runBrief() {
    setBrief('loading')
    const res = await call('generate_brief')
    if (!res?.ok) {
      setBrief(
        res?.error === 'NO_API_KEY'
          ? 'Add an Anthropic API key to generate a brief.'
          : `Could not generate a brief (${res?.error ?? 'BRIDGE_ERROR'}).`
      )
      return
    }
    if (res.status === 'no_new') {
      setBrief('no_new')
      return
    }
    setBrief({ summary: res.summary, generated_at: res.generated_at })
  }

  async function installService() {
    const res = await call('install_service')
    if (!res.ok) setError(`Could not start the service (${res.error}).`)
    refresh()
  }

  async function saveApiKey(key) {
    const res = await call('set_api_key', key)
    if (res.ok) refresh()
    return res
  }

  if (state === null) {
    return <div className="app"><div className="empty">Connecting to the TextForMe service…</div></div>
  }

  return (
    <div className="app">
      <header className="header">
        <div className="title">Text<span>For</span>Me</div>
        <button
          className="logs-btn brief-btn"
          style={{ marginLeft: 'auto' }}
          onClick={runBrief}
          disabled={!connected || brief === 'loading'}
        >
          {brief === 'loading' ? 'Briefing…' : 'Brief me'}
        </button>
        <button className="logs-btn" onClick={() => setShowPrompts(true)}>Prompts</button>
        <button className="logs-btn" onClick={() => setShowLogs(true)}>Logs</button>
      </header>

      {!connected && (
        <div className="banner">
          The background service is not running — contacts and settings are read-only until it starts.
          <button onClick={installService}>Start service</button>
        </div>
      )}
      {error && <div className="banner">{error}</div>}

      <div className="body">
        <section className="contacts">
          <div className="pane-title">Contacts</div>
          <ContactList
            contacts={contacts}
            selectedGuid={selectedGuid}
            disabled={!connected}
            onSelect={setSelectedGuid}
            onToggle={toggleAi}
            onToggleTimer={toggleTimer}
          />
        </section>
        <section className="right-col">
          <div className="settings">
            <div className="pane-title">Settings</div>
            <SettingsPanel
              settings={state.settings ?? {}}
              hasApiKey={Boolean(state.has_api_key)}
              disabled={!connected}
              onChange={setSetting}
              onSaveApiKey={saveApiKey}
            />
          </div>
          <div className="note-panel">
            <div className="pane-title">Contact note</div>
            <NotePanel contact={selected} disabled={!connected} onSave={saveNote} />
          </div>
        </section>
      </div>

      {brief !== null && (
        <BriefOverlay
          state={brief}
          onClose={() => setBrief(null)}
          onRegenerate={runBrief}
        />
      )}
      {showLogs && <LogsOverlay onClose={() => setShowLogs(false)} />}
      {showPrompts && (
        <PromptsOverlay
          disabled={!connected}
          onClose={() => setShowPrompts(false)}
          onSaved={refresh}
        />
      )}
    </div>
  )
}
