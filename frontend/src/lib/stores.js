// A store's chart colour is fixed by the order it was first seen (validated slots s1..s5), so it
// never changes between pages or when a filter hides another store.
import { useQuery } from '@tanstack/react-query'
import { get } from './api'

const SLOTS = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)', 'var(--s5)']

export function useStores() {
  const { data = [] } = useQuery({ queryKey: ['stores'], queryFn: () => get('/stores'), staleTime: 60_000 })
  const byId = Object.fromEntries(data.map((s) => [s.id, s]))
  const color = (id) => {
    const s = byId[id]
    // past the validated slots a store folds into neutral "other" rather than reusing a hue
    if (!s || s.slot > SLOTS.length) return 'var(--ink-3)'
    return SLOTS[s.slot - 1]
  }
  return { stores: data, byId, color }
}
