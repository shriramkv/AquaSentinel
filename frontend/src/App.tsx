import { FileText, Fish, Gauge, Languages, MessagesSquare, Moon, Radar, Sun, Waves } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ErrorNote } from './components/ui'
import { I18nProvider, LANGS, useI18n, type Lang } from './i18n'
import { Ask } from './pages/Ask'
import { Benchmarks } from './pages/Benchmarks'
import { Live } from './pages/Live'
import { Reports } from './pages/Reports'
import { Zones } from './pages/Zones'
import { LiveProvider, useLive } from './store'

const TABS = [
  { id: 'live', label: 'nav.live', short: 'nav.live.s', Icon: Radar },
  { id: 'zones', label: 'nav.zones', short: 'nav.zones.s', Icon: Fish },
  { id: 'ask', label: 'nav.ask', short: 'nav.ask.s', Icon: MessagesSquare },
  { id: 'reports', label: 'nav.reports', short: 'nav.reports.s', Icon: FileText },
  { id: 'benchmarks', label: 'nav.bench', short: 'nav.bench.s', Icon: Gauge },
] as const
type TabId = (typeof TABS)[number]['id']

const readTab = (): TabId => (TABS.find((x) => x.id === location.hash.slice(1))?.id ?? 'live')
const osDark = () => matchMedia('(prefers-color-scheme: dark)').matches

function Shell() {
  const { live, connected, error } = useLive()
  const { t, lang, setLang } = useI18n()
  const [tab, setTab] = useState<TabId>(readTab)
  const [dark, setDark] = useState<boolean>(() => {
    const q = new URLSearchParams(location.search).get('theme')          // ?theme=light|dark for screenshots and demos
    if (q === 'light' || q === 'dark') return q === 'dark'
    try { const s = localStorage.getItem('theme'); if (s) return s === 'dark' } catch { /* storage blocked */ }
    return osDark()
  })
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light' }, [dark])
  useEffect(() => { const f = () => { setTab(readTab()); scrollTo(0, 0) }; addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, [])
  const toggle = () => setDark((d) => { try { localStorage.setItem('theme', d ? 'light' : 'dark') } catch { /* storage blocked */ } return !d })

  return (
    <div className="min-h-full flex flex-col">
      <header className="sticky top-0 z-[1100] border-b border-line" style={{ background: 'color-mix(in srgb, var(--surface) 88%, transparent)', backdropFilter: 'blur(10px)', paddingTop: 'env(safe-area-inset-top)' }}>
        <div className="max-w-[1500px] mx-auto px-3 sm:px-6 py-2 sm:py-2.5 flex items-center gap-x-4 gap-y-2 flex-wrap">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className="inline-flex items-center justify-center w-8 h-8 rounded-[10px] shrink-0" style={{ background: 'var(--accent)', color: 'var(--accent-ink)' }}><Waves size={18} /></span>
            <div className="leading-tight min-w-0">
              <div className="font-semibold text-[0.95rem]">AquaSentinel</div>
              <div className="text-[0.7rem] text-muted truncate">{live ? live.lake.name : t('hdr.connecting')}</div>
            </div>
          </div>
          {/* Tablets and up: tabs in the header (own row until there is room beside the logo) */}
          <nav className="hidden md:flex gap-1 overflow-x-auto order-3 w-full xl:order-none xl:w-auto" aria-label="Sections">
            {TABS.map(({ id, label, Icon }) => (
              <a key={id} href={`#${id}`} aria-current={tab === id ? 'page' : undefined}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[0.8125rem] font-medium no-underline whitespace-nowrap transition-colors hover:bg-surface-2"
                style={tab === id ? { background: 'var(--accent-soft)', color: 'var(--ink)' } : { color: 'var(--ink-2)' }}>
                <Icon size={14} />{t(label)}
              </a>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-1.5 justify-end">
            {live?.config.simulated && <span className="chip hidden sm:inline-flex" title={t('hdr.simTitle')}>{t('hdr.sim')}</span>}
            {live && <span className="chip hidden sm:inline-flex">
              <i className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: live.config.gemini_ready ? 'var(--good)' : 'var(--axis)' }} />
              {live.config.gemini_ready ? live.config.gemini_model : t('hdr.geminiOff')}
            </span>}
            <span className="chip" role="status">
              <i className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: connected ? 'var(--good)' : 'var(--critical)' }} />{connected ? t('hdr.live') : t('hdr.offline')}
            </span>
            <label className="btn btn-sm !gap-1 !pr-1 cursor-pointer" title={t('hdr.language')}>
              <Languages size={14} aria-hidden />
              <select className="bg-transparent border-0 outline-none font-[inherit] text-[inherit] cursor-pointer max-w-24" style={{ fontSize: 'inherit' }}
                aria-label={t('hdr.language')} value={lang} onChange={(e) => setLang(e.target.value as Lang)}>
                {LANGS.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
              </select>
            </label>
            <button className="icon-btn" onClick={toggle} aria-label={dark ? t('hdr.toLight') : t('hdr.toDark')}>{dark ? <Sun size={15} /> : <Moon size={15} />}</button>
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-[1500px] w-full mx-auto px-3 sm:px-6 py-3 sm:py-5 pb-24 md:pb-6">
        {live?.config.simulated && <div className="sm:hidden mb-3 flex gap-1.5 flex-wrap">
          <span className="chip">{t('hdr.sim')}</span>
          <span className="chip">{live.config.gemini_ready ? live.config.gemini_model : t('hdr.geminiOff')}</span>
        </div>}
        {error && !live && <ErrorNote>{t('app.noBackend', { error })}</ErrorNote>}
        {!error && !live && <p className="text-sm text-muted">{t('app.loading')}</p>}
        {live && (tab === 'live' ? <Live dark={dark} /> : tab === 'zones' ? <Zones /> : tab === 'ask' ? <Ask /> : tab === 'reports' ? <Reports /> : <Benchmarks />)}
      </main>

      {/* Phones: tabs along the bottom edge, within thumb reach */}
      <nav className="tabbar md:hidden" aria-label="Sections">
        {TABS.map(({ id, short, Icon }) => (
          <a key={id} href={`#${id}`} aria-current={tab === id ? 'page' : undefined}><Icon size={19} /><span>{t(short)}</span></a>
        ))}
      </nav>
    </div>
  )
}

export default function App() {
  return <I18nProvider><LiveProvider><Shell /></LiveProvider></I18nProvider>
}
