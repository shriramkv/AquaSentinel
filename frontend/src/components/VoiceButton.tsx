import { Loader2, Mic, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useI18n } from '../i18n'

/* eslint-disable @typescript-eslint/no-explicit-any */
const SR: any = typeof window !== 'undefined' ? ((window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition) : undefined
const canRecord = typeof window !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== 'undefined'
const MAX_RECORD_MS = 30_000

interface Props {
  value: string
  onChange: (text: string) => void
  onError: (message: string | null) => void
  geminiReady: boolean
  disabled?: boolean
}

/**
 * Microphone button for a text field. Speech is recognised in the dashboard's language.
 * Uses the browser's speech recogniser where it exists (Chrome, Edge, Safari); elsewhere it
 * records a short clip and has Gemini transcribe it.
 */
export function VoiceButton({ value, onChange, onError, geminiReady, disabled }: Props) {
  const { t, speech } = useI18n()
  const [state, setState] = useState<'idle' | 'listening' | 'transcribing'>('idle')
  const stop = useRef<(() => void) | null>(null)
  const base = useRef('')
  useEffect(() => () => stop.current?.(), [])

  const mode: 'browser' | 'gemini' | 'none' = SR ? 'browser' : canRecord && geminiReady ? 'gemini' : 'none'
  const join = (text: string) => (base.current && text ? `${base.current} ${text}` : base.current || text)

  const startBrowser = () => {
    const rec = new SR()
    rec.lang = speech
    rec.interimResults = true
    rec.continuous = false
    let heard = false
    rec.onresult = (ev: any) => {
      heard = true
      onChange(join(Array.from(ev.results as ArrayLike<any>).map((r) => r[0].transcript).join('').trim()))
    }
    rec.onerror = (ev: any) => {
      if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') onError(t('voice.denied'))
      else if (ev.error === 'no-speech') onError(t('voice.nospeech'))
      else if (ev.error !== 'aborted') onError(`${t('voice.unsupported')} (${ev.error})`)
    }
    rec.onend = () => { setState('idle'); stop.current = null; if (!heard) onChange(base.current) }
    stop.current = () => rec.stop()
    rec.start()
    setState('listening')
  }

  const startRecording = async () => {
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      return onError(t('voice.denied'))
    }
    const type = ['audio/webm', 'audio/mp4', 'audio/ogg'].find((m) => MediaRecorder.isTypeSupported(m))
    const rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined)
    const chunks: Blob[] = []
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
    rec.onstop = async () => {
      clearTimeout(timer)
      stream.getTracks().forEach((tr) => tr.stop())
      stop.current = null
      setState('transcribing')
      try {
        const mime = (rec.mimeType || type || 'audio/webm').split(';')[0]
        const form = new FormData()
        form.append('file', new Blob(chunks, { type: mime }), 'speech')
        const { text } = await api<{ text: string }>('/transcribe', { form })
        if (text) onChange(join(text)); else onError(t('voice.nospeech'))
      } catch (e) {
        onError((e as Error).message)
      } finally { setState('idle') }
    }
    const timer = setTimeout(() => rec.state === 'recording' && rec.stop(), MAX_RECORD_MS)
    stop.current = () => rec.state === 'recording' && rec.stop()
    rec.start()
    setState('listening')
  }

  const click = () => {
    onError(null)
    if (state === 'listening') return stop.current?.()
    if (!window.isSecureContext) return onError(t('voice.insecure'))
    base.current = value.trim()
    try {
      if (mode === 'browser') startBrowser(); else void startRecording()
    } catch (e) {
      setState('idle')
      onError(`${t('voice.unsupported')} (${(e as Error).message})`)
    }
  }

  const label = mode === 'none' ? t('voice.unsupported') : state === 'listening' ? t('voice.stop') : state === 'transcribing' ? t('voice.transcribing') : t('voice.start')
  return (
    <button type="button" className={`icon-btn ${state === 'listening' ? 'mic-live' : ''}`} data-live={state === 'listening'} onClick={click}
      disabled={disabled || mode === 'none' || state === 'transcribing'} aria-label={label} title={label} aria-pressed={state === 'listening'}>
      {state === 'transcribing' ? <Loader2 size={16} className="animate-spin" /> : state === 'listening' ? <Square size={14} fill="currentColor" /> : <Mic size={16} />}
    </button>
  )
}
