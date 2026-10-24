// The shelf tag: description on top, unit price boxed at left, the price large at right, and
// a yellow band when the receipt shows it was bought on sale.
import { dollars, shortDate } from '../lib/format'
import { Money, UnitBox } from './bits'

export default function PriceTag({ name, meta, price, unit, unitLabel, saved, regular, size = 'md', as = 'div', onClick, children }) {
  const Tag = as
  const big = size === 'lg'
  return (
    <Tag className={`tag ${big ? 'tag--lg' : ''}`} onClick={onClick} {...(as === 'button' ? { type: 'button' } : {})}
      style={as === 'button' ? { textAlign: 'left', cursor: 'pointer', font: 'inherit', color: 'inherit', width: '100%' } : undefined}>
      <div className="tag__desc">
        <div className="tag__name">{name}</div>
        {meta && <div className="tag__meta">{meta}</div>}
      </div>
      <div className="tag__unit"><UnitBox value={unit} label={unitLabel} size={big ? 'lg' : undefined} /></div>
      <div className="tag__price"><Money value={price} size={big ? 'xl' : 'md'} /></div>
      {saved > 0 && (
        <div className="tag__deal">
          <span>Saved {dollars(saved)}</span>
          {regular ? <span>was {dollars(regular)}</span> : null}
        </div>
      )}
      {children}
    </Tag>
  )
}

export function lastMeta(last) {
  if (!last) return null
  return [last.store || 'No receipt', shortDate(last.date)].filter(Boolean).join(', ')
}
