import { useSyncExternalStore } from 'react'
import { api } from '../api/client'
import type { Onboarding } from '../types'

/** The person's onboarding state (wizard, checklist, tours, tips), shared by every component that
 *  needs it; loaded once and refreshed after each change. */
let state: Onboarding | null = null
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((l) => l())

export function setOnboarding(next: Onboarding) { state = next; emit() }

export async function loadOnboarding(): Promise<Onboarding | null> {
  try { setOnboarding(await api.onboarding.get()) } catch { /* the console works without it */ }
  return state
}

export async function updateOnboarding(patch: Parameters<typeof api.onboarding.update>[0]) {
  setOnboarding(await api.onboarding.update(patch))
}

export function useOnboarding(): Onboarding | null {
  return useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l) }, () => state)
}

/** A first-time tip: shown until the person says "Got it". */
export function tipSeen(id: string): boolean {
  return !state || state.tips_seen.includes(id)
}

export function seeTip(id: string) {
  if (state && !state.tips_seen.includes(id)) setOnboarding({ ...state, tips_seen: [...state.tips_seen, id] })
  api.onboarding.update({ tip_seen: id }).then(setOnboarding).catch(() => {})
}

export type TourChapter = 'run' | 'app' | 'start'

/** Start a chapter of the guided tour (the Tour component listens). */
export function startTour(chapter: TourChapter) {
  window.dispatchEvent(new CustomEvent('aurora:tour', { detail: chapter }))
}

/** Open a page of the documentation in the console. */
export function openDoc(path: string) {
  window.dispatchEvent(new CustomEvent('aurora:open-doc', { detail: path }))
}

let traced = false
/** The person opened a result's provenance (the checklist's "trace a number"). */
export function noteTraced() {
  if (traced || !state || state.checklist.find((c) => c.key === 'trace')?.done) return
  traced = true
  api.onboarding.update({ traced: true }).then(setOnboarding).catch(() => {})
}
