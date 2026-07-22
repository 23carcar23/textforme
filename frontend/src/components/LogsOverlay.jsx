import React, { useEffect, useRef, useState } from 'react'
import { call } from '../bridge.js'

const REFRESH_MS = 2000

export default function LogsOverlay({ onClose }) {
  const [lines, setLines] = useState(null)
  const [clearing, setClearing] = useState(false)
  const preRef = useRef(null)
  const pinnedToEnd = useRef(true)

  useEffect(() => {
    let alive = true
    async function load() {
      const res = await call('get_logs')
      if (alive && res.ok) setLines(res.lines ?? [])
    }
    load()
    const timer = setInterval(load, REFRESH_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    // jump to the newest entries as soon as they load, then stay pinned to
    // the end while more arrive (unless the user has scrolled up to read)
    const pre = preRef.current
    if (pre && pinnedToEnd.current) {
      pre.scrollTop = pre.scrollHeight
    }
  }, [lines])

  function handleScroll() {
    const pre = preRef.current
    if (!pre) return
    pinnedToEnd.current = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 60
  }

  async function handleClear() {
    setClearing(true)
    const res = await call('clear_logs')
    if (res.ok) setLines([])
    setClearing(false)
  }

  return (
    <div className="logs-overlay" onClick={onClose}>
      <div className="logs-sheet" onClick={(e) => e.stopPropagation()}>
        <header>
          Daemon logs
          <button onClick={handleClear} disabled={clearing || !lines?.length}>
            Clear logs
          </button>
          <button onClick={onClose}>Close (Esc)</button>
        </header>
        <pre ref={preRef} onScroll={handleScroll}>
          {lines === null
            ? 'Loading…'
            : lines.length
              ? lines.join('\n')
              : '(no log entries yet — logs live in ~/Library/Logs/TextForMe)'}
        </pre>
      </div>
    </div>
  )
}
