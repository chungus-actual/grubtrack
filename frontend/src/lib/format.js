// Money, unit prices, dates and counts, written the way a shelf tag or a person would say them.

export function dollars(v, { cents = true } = {}) {
  if (v === null || v === undefined || Number.isNaN(v)) return ''
  const n = Number(v)
  const s = Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: cents ? 2 : 0,
    maximumFractionDigits: cents ? 2 : 0,
  })
  return `${n < 0 ? '−' : ''}$${s}`
}

export function compactDollars(v) {
  if (v === null || v === undefined) return ''
  const n = Number(v)
  if (Math.abs(n) >= 10000) return `$${(n / 1000).toFixed(1).replace(/\.0$/, '')}K`
  if (Math.abs(n) >= 1000) return `$${Math.round(n).toLocaleString('en-US')}`
  return dollars(n, { cents: Math.abs(n) < 100 })
}

// split for the raised-cents treatment: 6.49 -> ['6', '49']
export function splitPrice(v) {
  const n = Math.round(Number(v) * 100)
  const whole = Math.floor(Math.abs(n) / 100)
  const c = String(Math.abs(n) % 100).padStart(2, '0')
  return [(n < 0 ? '−' : '') + whole.toLocaleString('en-US'), c]
}

// unit price on a shelf tag: under a dollar it's cents (12.8¢), otherwise dollars ($2.29)
export function unitText(v) {
  if (v === null || v === undefined) return ''
  if (v < 1) {
    const c = v * 100
    return `${c < 10 ? c.toFixed(1) : c.toFixed(c < 100 ? 1 : 0)}¢`.replace('.0¢', '¢')
  }
  return dollars(v)
}

export function perLabel(label) {
  if (!label) return ''
  if (label === 'pkg') return 'per pack'
  if (label === 'ea') return 'each'
  return `per ${label}`
}

export function pct(v, { sign = true, digits = 0 } = {}) {
  if (v === null || v === undefined) return ''
  const n = Number(v)
  const s = Math.abs(n).toFixed(digits)
  return `${sign ? (n > 0 ? '+' : n < 0 ? '−' : '') : ''}${s}%`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
  'October', 'November', 'December']
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

export function parseDay(s) {
  if (!s) return null
  const [y, m, d] = String(s).slice(0, 10).split('-').map(Number)
  if (!y || !m || !d) return null
  return new Date(y, m - 1, d)
}

export function today() {
  const t = new Date()
  return new Date(t.getFullYear(), t.getMonth(), t.getDate())
}

export function daysBetween(a, b) {
  return Math.round((b - a) / 86400000)
}

export function shortDate(s, { year = 'auto' } = {}) {
  const d = parseDay(s)
  if (!d) return ''
  const showYear = year === true || (year === 'auto' && d.getFullYear() !== today().getFullYear())
  return `${MONTHS[d.getMonth()]} ${d.getDate()}${showYear ? `, ${d.getFullYear()}` : ''}`
}

export function monthName(ym, long = false) {
  const [y, m] = ym.split('-').map(Number)
  return `${(long ? MONTHS_LONG : MONTHS)[m - 1]}${long && y !== today().getFullYear() ? ` ${y}` : ''}`
}

export function relDays(n) {
  if (n === null || n === undefined) return ''
  if (n === 0) return 'today'
  if (n === 1) return 'tomorrow'
  if (n === -1) return 'yesterday'
  if (n < 0) return `${-n} days ago`
  if (n < 14) return `in ${n} days`
  if (n < 60) return `in ${Math.round(n / 7)} weeks`
  return `in ${Math.round(n / 30)} months`
}

// the stamp under a date: "3 days left", "expired 4 days ago"
export function freshnessText(daysLeft) {
  if (daysLeft === null || daysLeft === undefined) return 'no date'
  if (daysLeft < 0) return daysLeft === -1 ? 'expired yesterday' : `expired ${-daysLeft}d ago`
  if (daysLeft === 0) return 'expires today'
  if (daysLeft === 1) return 'tomorrow'
  if (daysLeft < 14) return `${daysLeft} days`
  if (daysLeft < 60) return `${Math.round(daysLeft / 7)} weeks`
  return `${Math.round(daysLeft / 30)} months`
}

export function ago(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const s = (Date.now() - d.getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  const days = Math.round(s / 86400)
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`
  return shortDate(iso)
}

export function weekday(s) {
  const d = parseDay(s)
  return d ? DAYS[d.getDay()] : ''
}

export function plural(n, one, many) {
  return `${n.toLocaleString('en-US')} ${n === 1 ? one : many || `${one}s`}`
}

export function qtyText(q, unit) {
  if (!q && !unit) return ''
  const n = q ? Number(q) : null
  const qs = n === null ? '' : Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
  return [qs, unit].filter(Boolean).join(' ')
}

export const STORAGES = ['fridge', 'freezer', 'pantry', 'cupboard', 'counter', 'other']

export function cap(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''
}

// House style: no em dashes in anything shown, including what a model or agent writes.
export const plainDashes = (t) => (t || '').replace(/\s*\u2014\s*/g, ', ')
