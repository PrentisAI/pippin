/** In the paper reader: the sentences of the paper that what you saved before challenges or qualifies. Click one
 *  to see it in the PDF. */
import type { MemDissent } from '../../types'

export function DissentPanel({ data, onQuote }: { data: MemDissent | null | undefined; onQuote: (quote: string, page: number) => void }) {
  if (data === undefined) return <div className="rd-loading">Reading the paper against your memory…</div>
  if (data === null) return <div className="rd-note-warn">Your memory could not be read for this paper.</div>
  if (!data.items.length) return (
    <div className="cw-empty"><b>Nothing you saved argues with this paper</b>
      <span>{data.note || 'The claims in your memory nearest to it agree with it, or are about something else.'}</span></div>
  )
  return (
    <div className="mem-dissent">
      <span className="lit-sub">What you saved before, from other sources, against sentences of this paper.</span>
      {data.items.map((it, i) => (
        <div key={i} className={`mem-dis ${it.kind}`}>
          <button className="rv-quote" onClick={() => onQuote(it.quote, it.page)}>p. {it.page + 1}</button>
          <blockquote className="pl-quote">{it.quote}</blockquote>
          <span className="lit-sub">{it.units.length} of your saves {it.kind === 'challenges' ? 'challenge' : 'qualify'} this: {it.why}</span>
          {it.units.map((u) => <span key={u.unit_id} className="mem-dis-u">{u.claim}<span className="lit-sub"> · {u.sources} source{u.sources > 1 ? 's' : ''}</span></span>)}
        </div>
      ))}
    </div>
  )
}
