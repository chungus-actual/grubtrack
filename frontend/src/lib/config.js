// What this instance can do: which AI answers which job (by name), and whether recipes can be saved.
import { useQuery } from '@tanstack/react-query'
import { get } from './api'

const NONE = {
  version: '',
  ai: { model: null, agent: null, chat: null, extract: null, vision: false, send: null },
  recipes: { write: false },
}

export function useConfig() {
  const { data } = useQuery({ queryKey: ['config'], queryFn: () => get('/config'), staleTime: Infinity })
  return data || NONE
}
