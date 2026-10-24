// Detail sheets: a side panel on desktop, a bottom sheet on phones. Each one is addressable
// (?open=product:pr_123), so a receipt line can open its product and the back button closes it.
import { useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { X } from 'lucide-react'

export function useSheet() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('open') || ''
  const i = raw.indexOf(':')
  const kind = i > 0 ? raw.slice(0, i) : raw || null
  const id = i > 0 ? raw.slice(i + 1) : null
  const open = (k, v) => {
    const next = new URLSearchParams(params)
    next.set('open', v ? `${k}:${v}` : k)
    setParams(next, { replace: Boolean(kind) })
  }
  const close = () => {
    const next = new URLSearchParams(params)
    next.delete('open')
    setParams(next)
  }
  return { kind, id, open, close }
}

export default function Sheet({ title, onClose, children, footer, wide = false, actions }) {
  const ref = useRef(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  // once per opening: a refetch re-rendering the sheet must not move focus or re-run this
  useEffect(() => {
    const prev = document.activeElement
    const el = ref.current
    if (el && !el.contains(document.activeElement)) el.focus()  // keep a field's autoFocus
    const onKey = (e) => {
      if (e.key === 'Escape') closeRef.current()
      if (e.key === 'Tab' && el) {
        const f = el.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
        if (!f.length) return
        const first = f[0]
        const last = f[f.length - 1]
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
      if (prev && document.contains(prev) && !el?.contains(prev)) prev.focus()
    }
  }, [])
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <section className={`sheet ${wide ? 'sheet--wide' : ''}`} role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : undefined} tabIndex={-1} ref={ref}>
        <div className="sheet__grab" aria-hidden="true" />
        <header className="sheet__head">
          <h2>{title}</h2>
          <div className="hstack" style={{ gap: 4, flexWrap: 'nowrap' }}>
            {actions}
            <button className="iconbtn" onClick={onClose} aria-label="Close"><X /></button>
          </div>
        </header>
        <div className="sheet__body">{children}</div>
        {footer && <footer className="sheet__foot">{footer}</footer>}
      </section>
    </>
  )
}
