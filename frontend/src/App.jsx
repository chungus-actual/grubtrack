import { lazy, Suspense, useEffect, useState } from 'react'
import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChefHat, ListChecks, Refrigerator, Receipt, Tag } from 'lucide-react'
import { get } from './lib/api'
import { useSheet } from './components/Sheet'
import { Loading } from './components/bits'
import Pantry from './pages/Pantry'

const Prices = lazy(() => import('./pages/Prices'))
const Spend = lazy(() => import('./pages/Spend'))
const List = lazy(() => import('./pages/List'))
const Cook = lazy(() => import('./pages/Cook'))
const ItemSheet = lazy(() => import('./sheets/ItemSheet'))
const ProductSheet = lazy(() => import('./sheets/ProductSheet'))
const ReceiptSheet = lazy(() => import('./sheets/ReceiptSheet'))
const StoreSheet = lazy(() => import('./sheets/StoreSheet'))
const AddItemSheet = lazy(() => import('./sheets/AddItemSheet'))
const RecipeSheet = lazy(() => import('./sheets/RecipeSheet'))
const NewReceiptSheet = lazy(() => import('./sheets/NewReceiptSheet'))

const NAV = [
  { to: '/', label: 'Pantry', icon: Refrigerator, end: true },
  { to: '/prices', label: 'Prices', icon: Tag },
  { to: '/spend', label: 'Spend', icon: Receipt },
  { to: '/list', label: 'List', icon: ListChecks },
  { to: '/cook', label: 'Cook', icon: ChefHat },
]

function ThemeToggle() {
  const [t, setT] = useState(() => document.documentElement.dataset.theme || 'system')
  const pick = (v) => {
    setT(v)
    if (v === 'system') delete document.documentElement.dataset.theme
    else document.documentElement.dataset.theme = v
    try { v === 'system' ? localStorage.removeItem('gt.theme') : localStorage.setItem('gt.theme', v) } catch {}
  }
  return (
    <div className="theme-toggle" role="group" aria-label="Theme">
      {['system', 'light', 'dark'].map((v) => (
        <button key={v} aria-pressed={t === v} onClick={() => pick(v)}>{v === 'system' ? 'Auto' : v === 'light' ? 'Light' : 'Dark'}</button>
      ))}
    </div>
  )
}

function SheetHost() {
  const { kind, id, close } = useSheet()
  if (!kind) return null
  const S = { item: ItemSheet, product: ProductSheet, receipt: ReceiptSheet, store: StoreSheet, add: AddItemSheet, recipe: RecipeSheet,
    newreceipt: NewReceiptSheet }[kind]
  if (!S) return null
  return <Suspense fallback={null}><S id={id} onClose={close} /></Suspense>
}

export default function App() {
  const loc = useLocation()
  const { data: list } = useQuery({ queryKey: ['shopping'], queryFn: () => get('/shopping'), staleTime: 30_000 })
  const counts = { '/list': list?.open?.length || null }
  useEffect(() => { window.scrollTo(0, 0) }, [loc.pathname])
  return (
    <div className="shell">
      <aside className="rail">
        <a className="brand" href="/"><img src="/icon.svg" alt="" /><span>GrubTrack</span></a>
        <nav aria-label="Sections">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end}>
              <Icon aria-hidden="true" />{label}
              {counts[to] ? <span className="count">{counts[to]}</span> : null}
            </NavLink>
          ))}
        </nav>
        <div className="rail-foot"><ThemeToggle /></div>
      </aside>
      <main className="main">
        <Suspense fallback={<Loading />}>
          <Routes>
            <Route path="/" element={<Pantry />} />
            <Route path="/prices" element={<Prices />} />
            <Route path="/spend" element={<Spend />} />
            <Route path="/list" element={<List />} />
            <Route path="/cook" element={<Cook />} />
            {/* v1 paths people may have bookmarked */}
            {['/dashboard', '/inventory', '/expiring', '/bar'].map((p) => <Route key={p} path={p} element={<Navigate to="/" replace />} />)}
            {['/meals', '/recipes'].map((p) => <Route key={p} path={p} element={<Navigate to="/cook" replace />} />)}
            <Route path="/shopping" element={<Navigate to="/list" replace />} />
            <Route path="*" element={<Pantry />} />
          </Routes>
        </Suspense>
      </main>
      <nav className="tabbar" aria-label="Sections">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end}><Icon aria-hidden="true" />{label}</NavLink>
        ))}
      </nav>
      <SheetHost />
    </div>
  )
}
