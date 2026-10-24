// Confirmations that say what happened, with an Undo where one exists.
import { createContext, useCallback, useContext, useRef, useState } from 'react'

const Ctx = createContext(() => {})

export function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const seq = useRef(0)
  const push = useCallback((text, action) => {
    const id = ++seq.current
    setItems((xs) => [...xs.slice(-2), { id, text, action }])
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), action ? 7000 : 3500)
  }, [])
  const dismiss = (id) => setItems((xs) => xs.filter((x) => x.id !== id))
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div className="toast" key={t.id}>
            <span>{t.text}</span>
            {t.action && (
              <button onClick={() => { dismiss(t.id); t.action.run() }}>{t.action.label}</button>
            )}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  )
}

export const useToast = () => useContext(Ctx)
