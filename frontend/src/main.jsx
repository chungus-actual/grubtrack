import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@fontsource-variable/archivo/wdth.css'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/600.css'
import './styles/tokens.css'
import './styles/app.css'
import App from './App'
import { ToastProvider } from './components/Toasts'
import { reloadIfStale } from './lib/api'

const qc = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15_000, refetchOnWindowFocus: true, retry: 1, placeholderData: (prev) => prev },
  },
})

// a tab left open across a deploy reloads itself when nothing is in progress
document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && reloadIfStale())
setInterval(reloadIfStale, 60_000)

try {
  const t = localStorage.getItem('gt.theme')
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t
} catch {}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <ToastProvider>
          <App />
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
