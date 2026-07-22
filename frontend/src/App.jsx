import React, { useCallback, useEffect, useState } from 'react'
import { call } from './bridge.js'
import ContactList from './components/ContactList.jsx'
import SettingsPanel from './components/SettingsPanel.jsx'
import NotePanel from './components/NotePanel.jsx'
import LogsOverlay from './components/LogsOverlay.jsx'
import PromptsOverlay from './components/PromptsOverlay.jsx'

const POLL_MS = 3000

export default function App() {
  const [state, setState] = useState(null) // null until first get_state resolves
  const [selectedGuid, setSelectedGuid] = useState(null)
  const [showLogs, setShowLogs] = useState(false)
  const [showPrompts, setShowPrompts] = useState(false)
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
        <div className={`status ${connected ? 'running' : ''}`}>
          <span className="dot" aria-hidden="true" />
          <span>{connected ? 'Service running' : 'Service stopped'}</span>
          {connected && state.status?.replies_last_hour != null && (
            <span className="meta">{state.status.replies_last_hour} replies past hour</span>
          )}
        </div>
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
