import { MessagesSquare, Send } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { Card, ErrorNote, GeminiOff, Spinner, ToolCalls } from '../components/ui'
import { VoiceButton } from '../components/VoiceButton'
import { useI18n } from '../i18n'
import { useLive } from '../store'
import type { ToolCall } from '../types'

interface Turn { role: 'user' | 'assistant'; text: string; tool_calls?: ToolCall[]; error?: boolean }
const SUGGESTIONS = ['ask.s1', 'ask.s2', 'ask.s3', 'ask.s4', 'ask.s5'] as const

export function Ask() {
  const { live } = useLive()
  const { t } = useI18n()
  const [voiceErr, setVoiceErr] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const session = useRef<string | undefined>(undefined)
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [turns, busy])
  if (!live) return null
  const ready = live.config.gemini_ready

  const send = async (q: string) => {
    if (!q.trim() || busy) return
    setTurns((prev) => [...prev, { role: 'user', text: q.trim() }])
    setText(''); setBusy(true)
    try {
      const r = await api<{ session_id: string; answer: string; tool_calls: ToolCall[] }>('/ask', { body: { question: q.trim(), session_id: session.current } })
      session.current = r.session_id
      setTurns((prev) => [...prev, { role: 'assistant', text: r.answer || t('ask.empty'), tool_calls: r.tool_calls }])
    } catch (e) {
      setTurns((prev) => [...prev, { role: 'assistant', text: (e as Error).message, error: true }])
    } finally { setBusy(false) }
  }

  return (
    <div className="max-w-3xl mx-auto">
      <Card title={<><MessagesSquare size={15} className="shrink-0" /> {t('nav.ask')}</>} right={ready ? <span className="chip">{live.config.gemini_model}</span> : undefined}>
        <p className="text-xs text-ink-2 m-0 mb-3">{t('ask.intro')}{live.config.simulated && ` ${t('ask.sim')}`}</p>
        {!ready && <GeminiOff what={t('ask.off')} />}
        <div className="space-y-3 min-h-32 max-h-[52dvh] overflow-y-auto pr-1" aria-live="polite">
          {turns.length === 0 && ready && (
            <div className="flex flex-wrap gap-1.5">{SUGGESTIONS.map((s) => <button key={s} className="btn btn-sm !whitespace-normal text-left !justify-start" onClick={() => send(t(s))}>{t(s)}</button>)}</div>
          )}
          {turns.map((t, i) => t.role === 'user' ? (
            <div key={i} className="flex justify-end"><div className="max-w-[85%] rounded-2xl rounded-br-md px-3.5 py-2 text-sm" style={{ background: 'var(--accent)', color: 'var(--accent-ink)' }}>{t.text}</div></div>
          ) : t.error ? <ErrorNote key={i}>{t.text}</ErrorNote> : (
            <div key={i} className="max-w-[92%] rounded-2xl rounded-bl-md bg-surface-2 px-3.5 py-2.5 text-sm leading-relaxed">
              <div className="prose-sm"><Markdown remarkPlugins={[remarkGfm]}>{t.text}</Markdown></div>
              <ToolCalls calls={t.tool_calls} />
            </div>
          ))}
          {busy && <div className="inline-flex items-center gap-2 text-xs text-muted"><Spinner /> {t('ask.querying')}</div>}
          <div ref={end} />
        </div>
        <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); send(text) }}>
          <input className="field min-w-0" value={text} onChange={(e) => setText(e.target.value)} placeholder={t('ask.placeholder')} aria-label={t('ask.question')} disabled={!ready || busy} maxLength={600} />
          <VoiceButton value={text} onChange={setText} onError={setVoiceErr} geminiReady={ready} disabled={!ready || busy} />
          <button className="icon-btn btn-primary" disabled={!ready || busy || text.trim().length < 2} aria-label={t('ask.send')} title={t('ask.send')}><Send size={15} /></button>
        </form>
        <div className="mt-2"><ErrorNote>{voiceErr}</ErrorNote></div>
      </Card>
    </div>
  )
}
