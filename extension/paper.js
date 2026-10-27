// On a paper's page (arXiv, OpenReview, ACL Anthology, Semantic Scholar, a DOI): how it relates to your research
// questions and to what you saved, with saving it to your memory and to Zotero. It stays a small tab when the paper
// has nothing to do with your work.
(() => {
  if (window.__auroraPaper) return
  window.__auroraPaper = true
  const host = document.createElement('div')
  host.style.cssText = 'position:fixed;top:96px;right:0;z-index:2147483646'
  const root = host.attachShadow({ mode: 'open' })
  document.documentElement.appendChild(host)
  root.innerHTML = `<style>
    :host{all:initial}*{box-sizing:border-box;font-family:system-ui,-apple-system,"PingFang SC","Noto Sans SC",sans-serif}
    .tab{background:#2d6cdf;color:#fff;border:0;border-radius:10px 0 0 10px;padding:10px 8px;cursor:pointer;font-size:12px;writing-mode:vertical-rl}
    .card{width:320px;max-height:70vh;overflow:auto;background:#fff;color:#1f2328;border:1px solid #d7dbe3;border-right:0;border-radius:12px 0 0 12px;box-shadow:0 8px 28px rgba(0,0,0,.15);padding:14px;font-size:13px;line-height:1.5}
    .head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}.head b{font-size:14px}
    .x{border:0;background:none;font-size:18px;cursor:pointer;color:#667}
    .q{border-left:3px solid #2d6cdf;padding:4px 8px;margin:8px 0}.q b{color:#2d6cdf}
    .st{font-size:11px;border:1px solid #d7dbe3;border-radius:6px;padding:0 6px;margin-left:4px;color:#556}
    .st.challenges{color:#b45309;border-color:#f3c486}.u{background:#f6f7f9;border-radius:8px;padding:6px 8px;margin:6px 0}
    .sub{color:#667;font-size:12px}.row{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
    button.b{border:1px solid #2d6cdf;background:#2d6cdf;color:#fff;border-radius:8px;padding:5px 10px;cursor:pointer;font-size:12.5px}
    button.b.q2{background:#fff;color:#2d6cdf}button.b:disabled{opacity:.5;cursor:default}
    textarea{width:100%;font:11px ui-monospace,monospace;margin-top:6px}
  </style><button class="tab" hidden>AURORA</button><div class="card" hidden></div>`
  const tab = root.querySelector('.tab')
  const card = root.querySelector('.card')
  const esc = (s) => String(s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  const STANCE = { supports: 'supports', challenges: 'challenges', adds: 'adds', background: 'background' }
  let data = null

  const open = (yes) => { card.hidden = !yes; tab.hidden = yes }
  tab.onclick = () => open(true)

  const render = (extra = '') => {
    const d = data
    const qs = d.questions.map((q) => `<div class="q"><b>${esc(q.label)}</b><span class="st ${esc(q.stance)}">${esc(STANCE[q.stance] || q.stance)}</span>
      <div>${esc(q.how || q.text)}</div></div>`).join('')
    const us = d.units.map((u) => `<div class="u">${esc(u.relation || '')}<div class="sub">${esc(u.claim)}</div></div>`).join('')
    const z = d.zotero
    card.innerHTML = `<div class="head"><b>AURORA</b><button class="x" title="Close">×</button></div>
      ${d.paper ? `<div class="sub">${esc(d.paper.byline || '')}</div>` : ''}
      ${d.quiet ? '<div class="sub">It does not bear on your research questions or on what you saved.</div>' : ''}
      ${qs ? '<div class="sub" style="margin-top:8px">Your questions</div>' + qs : ''}
      ${us ? '<div class="sub" style="margin-top:8px">What you saved before</div>' + us : ''}
      <div class="row">
        <button class="b" data-a="save" ${d.saved ? 'disabled' : ''}>${d.saved ? 'In your memory' : 'Save to memory'}</button>
        ${d.paper && z && z.connected ? `<button class="b q2" data-a="zotero" ${z.in_zotero ? 'disabled' : ''}>${z.in_zotero ? 'In Zotero' : 'Save to Zotero'}</button>` : ''}
      </div>${extra}`
    card.querySelector('.x').onclick = () => open(false)
    const s = card.querySelector('[data-a="save"]')
    if (s) s.onclick = async () => {
      s.disabled = true; s.textContent = 'Saving…'
      const r = await chrome.runtime.sendMessage({ type: 'aurora:save' })
      s.textContent = r && r.ok ? 'In your memory' : 'Could not save'
    }
    const zb = card.querySelector('[data-a="zotero"]')
    if (zb) zb.onclick = async () => {
      zb.disabled = true
      const r = await chrome.runtime.sendMessage({ type: 'aurora:zotero', paper_id: d.paper.paper_id })
      if (r && r.ok && r.data.saved) { zb.textContent = 'In Zotero'; return }
      zb.disabled = false
      const why = r && r.ok ? r.data.reason : (r && r.error) || 'Could not reach AURORA'
      render(`<div class="sub" style="margin-top:8px">${esc(why)}</div>${r && r.ok && r.data.bibtex ? `<textarea rows="6" readonly>${esc(r.data.bibtex)}</textarea>` : ''}`)
    }
  }

  chrome.runtime.sendMessage({ type: 'aurora:relate', url: location.href, title: document.title }).then((r) => {
    if (!r || !r.ok) return              // AURORA not reachable: stay out of the way
    data = r.data
    render()
    tab.hidden = false
    open(!data.quiet)                    // unrelated papers: only the small tab
  }).catch(() => {})
})()
