import type { ReactNode } from 'react'
import { seeTip, useOnboarding } from './store'

/** A short tip the first time something appears; "Got it" hides it for good (Help can show them again). */
export function FirstTip({ id, children, doc }: { id: string; children: ReactNode; doc?: string }) {
  const ob = useOnboarding()
  if (!ob || ob.tips_seen.includes(id) || !ob.wizard.done) return null
  return (
    <div className="first-tip" role="note">
      <div className="first-tip-text">{children}</div>
      <div className="first-tip-actions">
        {doc && <button className="link-btn" onClick={() => window.dispatchEvent(new CustomEvent('aurora:open-doc', { detail: doc }))}>More</button>}
        <button className="first-tip-ok" onClick={() => seeTip(id)}>Got it</button>
      </div>
    </div>
  )
}
