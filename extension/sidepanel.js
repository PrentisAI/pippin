// The side panel shows your memory panel from the AURORA console; it is told when what you are reading changes.
const frame = document.getElementById('panel')
const msg = document.getElementById('msg')

chrome.runtime.sendMessage({ type: 'aurora:panel-url' }).then(({ url }) => {
  frame.src = url
  frame.hidden = false
  msg.hidden = true
  frame.addEventListener('error', () => { msg.hidden = false; msg.textContent = `Cannot reach AURORA at ${url}. Check the address in the extension’s options.` })
})

chrome.runtime.onMessage.addListener((m) => {
  if (m.type === 'aurora:refresh' && frame.contentWindow) frame.contentWindow.postMessage({ type: 'aurora:refresh' }, '*')
})
