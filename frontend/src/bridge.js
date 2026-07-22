// Bridge to the Python side (pywebview js_api). Every call returns the
// {ok, ...} envelope produced by textforme.webui.bridge.Bridge.
//
// When the app runs in a plain browser (`npm run dev` without pywebview),
// window.pywebview never appears, so after a short wait we fall back to an
// in-memory demo API — the UI stays fully clickable for development.

const demoContacts = [
  { chat_guid: 'demo-1', display_name: 'Mom', address: '+1 (555) 010-2222', is_group: false, ai_enabled: true, reply_timer_enabled: true, reply_timer_remaining: 47, description: 'my very strict mom so be nice to her' },
  { chat_guid: 'demo-2', display_name: 'Alex Rivera', address: '+1 (555) 010-3333', is_group: false, ai_enabled: false, reply_timer_enabled: false, reply_timer_remaining: null, description: '' },
  { chat_guid: 'demo-3', display_name: 'Ski Trip 2026', address: '4 people', is_group: true, ai_enabled: false, reply_timer_enabled: false, reply_timer_remaining: null, description: '' },
  { chat_guid: 'demo-4', display_name: '', address: '+1 (555) 010-4444', is_group: false, ai_enabled: false, reply_timer_enabled: false, reply_timer_remaining: null, description: '' },
]

const demoSettings = {
  selected_model_id: 'claude-sonnet-5',
  global_ai_enabled: 'true',
  paused: 'false',
  context_message_limit: '10',
  system_prompt: '',
  persona_prompt: '',
  style_profile: '',
}

const DEMO_SYSTEM_DEFAULT =
  'You are an automated texting assistant replying on behalf of the phone\'s owner '
  + 'to {contact_name}. Output ONLY the reply text, under {max_chars} characters, '
  + 'matching the casual tone of the conversation. (Demo default — the real app '
  + 'ships the full safety-hardened prompt.)'

const demoLogs = [
  '2026-07-22 09:14:02 INFO daemon started',
  '2026-07-22 09:14:03 INFO watching chat.db',
]

// Tracks whether the demo has already produced a brief, so a second click
// exercises the "no new conversations" path just like the real daemon.
const demoBrief = { done: false }

const demoApi = {
  get_state: async () => {
    // Demo-only: let any running countdown visibly tick down between polls.
    for (const c of demoContacts) {
      if (c.reply_timer_remaining != null) {
        c.reply_timer_remaining = c.reply_timer_remaining > 3 ? c.reply_timer_remaining - 3 : 120
      }
    }
    return {
      ok: true,
      connected: true,
      status: { running: true, replies_last_hour: 3, last_error: '' },
      contacts: demoContacts,
      settings: demoSettings,
      has_api_key: true,
      service_installed: true,
    }
  },
  set_ai: async (guid, enabled) => {
    const c = demoContacts.find((c) => c.chat_guid === guid)
    if (c?.is_group) return { ok: false, error: 'GROUP_FORBIDDEN' }
    if (c) c.ai_enabled = enabled
    return { ok: true }
  },
  set_reply_timer: async (guid, enabled) => {
    const c = demoContacts.find((c) => c.chat_guid === guid)
    if (c?.is_group) return { ok: false, error: 'GROUP_FORBIDDEN' }
    if (c) {
      c.reply_timer_enabled = enabled
      c.reply_timer_remaining = enabled ? 120 : null
    }
    return { ok: true }
  },
  set_note: async (guid, description) => {
    const c = demoContacts.find((c) => c.chat_guid === guid)
    if (c) c.description = description
    return { ok: true }
  },
  set_setting: async (key, value) => {
    demoSettings[key] = value
    return { ok: true }
  },
  list_models: async () => ({
    ok: true,
    models: [
      { model_id: 'claude-sonnet-5', display_name: 'Claude Sonnet 5' },
      { model_id: 'claude-haiku-4-5-20251001', display_name: 'Claude Haiku 4.5' },
    ],
  }),
  get_logs: async () => ({
    ok: true,
    lines: demoLogs,
  }),
  clear_logs: async () => {
    demoLogs.length = 0
    return { ok: true }
  },
  install_service: async () => ({ ok: true }),
  generate_brief: async () => {
    await new Promise((r) => setTimeout(r, 700)) // mimic the model round-trip
    if (demoBrief.done) return { ok: true, status: 'no_new' }
    demoBrief.done = true
    return {
      ok: true,
      status: 'ok',
      generated_at: '2026-07-22T09:20:00+00:00',
      summary:
        '• Mom — asked what time you\'re coming for dinner Sunday; the AI said '
        + '"around 5". She may want a firmer answer.\n'
        + '• Alex Rivera — confirmed the meeting moved to Thursday 2pm. Nothing '
        + 'needs your attention.',
    }
  },
  get_prompts: async () => ({
    ok: true,
    system_prompt: demoSettings.system_prompt,
    persona_prompt: demoSettings.persona_prompt,
    style_profile: demoSettings.style_profile,
    system_prompt_default: DEMO_SYSTEM_DEFAULT,
  }),
  set_api_key: async (key) => {
    if (!String(key).startsWith('sk-ant-') || String(key).length < 20) {
      return { ok: false, error: 'BAD_KEY' }
    }
    demoContacts.hasKey = true
    return { ok: true }
  },
}

let apiPromise = null

function resolveApi() {
  if (window.pywebview?.api) return Promise.resolve(window.pywebview.api)
  return new Promise((resolve) => {
    window.addEventListener('pywebviewready', () => resolve(window.pywebview.api), { once: true })
    setTimeout(() => {
      if (!window.pywebview?.api) resolve(demoApi)
    }, 900)
  })
}

export async function call(method, ...args) {
  if (!apiPromise) apiPromise = resolveApi()
  const api = await apiPromise
  try {
    return await api[method](...args)
  } catch {
    return { ok: false, error: 'BRIDGE_ERROR' }
  }
}
