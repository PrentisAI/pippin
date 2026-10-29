import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

try {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
} catch (e) {
  const root = document.getElementById('root')
  if (root) {
    root.innerHTML = `<div style="padding:40px;color:#f87171;background:#0c0e14;font-family:monospace;height:100vh">
      <h2 style="color:#c8cdd8;margin-bottom:16px">Pippin failed to start</h2>
      <pre style="white-space:pre-wrap;font-size:13px">${e instanceof Error ? e.message : String(e)}</pre>
      <pre style="white-space:pre-wrap;font-size:11px;color:#6b7280;margin-top:12px">${e instanceof Error ? e.stack : ''}</pre>
    </div>`
  }
}
