// The posts on a followed page, as the site shows them to you. Run inside the page (it must not use anything from
// outside this function). Sites change their markup: each reader tries the known structure first, then falls back
// to any link to a post with some text around it.

export function EXTRACT(platform) {
  const clean = (s) => (s || '').replace(/\s+\n/g, '\n').replace(/[ \t]+/g, ' ').trim()
  const abs = (href) => { try { return new URL(href, location.href).href } catch { return '' } }
  const out = []
  const seen = new Set()
  const add = (id, url, title, text, author, links) => {
    id = id || url
    if (!id || seen.has(id) || (clean(title) + clean(text)).length < 20) return
    seen.add(id)
    out.push({ id, url, title: clean(title).slice(0, 300), text: clean(text).slice(0, 20000), author: clean(author).slice(0, 120),
               links: (links || []).slice(0, 20) })
  }

  if (platform === 'zhihu') {
    document.querySelectorAll('.ContentItem, .List-item, .TopstoryItem, .AnswerItem, .ArticleItem').forEach((el) => {
      const a = el.querySelector('h2 a, .ContentItem-title a, a[href*="/question/"], a[href*="/p/"]')
      const url = a ? abs(a.getAttribute('href')) : ''
      const meta = el.querySelector('meta[itemprop="url"]')
      const id = (el.getAttribute('data-zop') && (JSON.parse(el.getAttribute('data-zop') || '{}').itemId || '')) || url || (meta && meta.content)
      const title = (el.querySelector('h2, .ContentItem-title') || {}).innerText || ''
      const text = (el.querySelector('.RichContent-inner, .RichText') || el).innerText || ''
      const author = (el.querySelector('.AuthorInfo-name, .UserLink-link') || {}).innerText || ''
      add(String(id), url || (meta && meta.content) || location.href, title, text, author)
    })
  } else if (platform === 'x') {
    document.querySelectorAll('article[data-testid="tweet"], article').forEach((el) => {
      const a = el.querySelector('a[href*="/status/"]')
      const url = a ? abs(a.getAttribute('href')).replace(/\/(analytics|photo\/\d+)$/, '') : ''
      const m = url.match(/status\/(\d+)/)
      const text = (el.querySelector('[data-testid="tweetText"]') || el).innerText || ''
      const author = (el.querySelector('[data-testid="User-Name"]') || {}).innerText || ''
      // the links in the post and its card (t.co, arXiv, HF papers…): what it is about
      const links = [...el.querySelectorAll('a[href]')].map((x) => abs(x.getAttribute('href')))
        .filter((h) => h && !/^https:\/\/(x|twitter)\.com\//.test(h))
      add(m ? m[1] : url, url, '', text, author.split('\n')[0], [...new Set(links)])
    })
  } else if (platform === 'xiaohongshu') {
    document.querySelectorAll('section.note-item, .note-item, .feeds-container section').forEach((el) => {
      const a = el.querySelector('a[href*="/explore/"], a[href*="/discovery/item/"], a.cover')
      const url = a ? abs(a.getAttribute('href')) : ''
      const m = url.match(/(?:explore|item)\/([0-9a-f]+)/)
      const title = (el.querySelector('.title, .footer .title span') || {}).innerText || ''
      const author = (el.querySelector('.author .name, .name') || {}).innerText || ''
      add(m ? m[1] : url, url, title, el.querySelector('.desc') ? el.querySelector('.desc').innerText : '', author)
    })
    // an opened note: its full text
    const note = document.querySelector('#detail-desc, .note-content')
    if (note) add(location.pathname, location.href, (document.querySelector('#detail-title, .note-content .title') || {}).innerText || document.title, note.innerText, '')
  }

  if (!out.length) {
    // any page: blocks with a link and enough text
    document.querySelectorAll('article, li, section').forEach((el) => {
      const a = el.querySelector('a[href]')
      const text = el.innerText || ''
      if (a && text.length > 80 && text.length < 20000) add(abs(a.getAttribute('href')), abs(a.getAttribute('href')), a.innerText, text, '')
    })
  }
  return out.slice(0, 40)
}
