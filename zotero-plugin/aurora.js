/* AURORA in Zotero 7.
 *
 * - Every paper you add is sent to your AURORA console and read in the background: its methods, findings and
 *   limitations wait for you in the item pane's AURORA section, with what your memory makes of it (its claims,
 *   conflicts with what you saved, the research questions it bears on).
 * - "Sentences your saves argue with": the sentences of the paper that what you saved before challenges, each
 *   opening the PDF at its page.
 * - Selecting text in the reader: "Save to AURORA" in the selection popup, and the passage goes to AURORA as what
 *   you are reading (when you allow it); "What my saves say": what you saved elsewhere that supports or challenges
 *   the sentence.
 * - "Mark them in the PDF": the disputed sentences as orange highlights in the margin (only when you allowed AURORA
 *   to annotate in Zotero; tagged aurora-dissent, and "Remove the marks" takes them away).
 * - "Save the card as a note": only when you allowed AURORA to write notes on Zotero items; logged in AURORA.
 */
Aurora = {
  id: null,
  rootURI: null,
  sectionID: null,
  observerID: null,
  windows: new Set(),

  init({ id, rootURI }) {
    this.id = id;
    this.rootURI = rootURI;
    this.sectionID = Zotero.ItemPaneManager.registerSection({
      paneID: "aurora",
      pluginID: id,
      header: { l10nID: "aurora-header", icon: rootURI + "icons/16.png" },
      sidenav: { l10nID: "aurora-sidenav", icon: rootURI + "icons/20.png" },
      onRender: ({ body }) => { body.textContent = "AURORA is reading…"; },
      onAsyncRender: async ({ body, item }) => { await this.render(body, item); },
    });
    this.observerID = Zotero.Notifier.registerObserver({
      notify: (event, type, ids) => { if (event === "add" && type === "item") this.onAdded(ids); },
    }, ["item"], "aurora");
    Zotero.Reader.registerEventListener("renderTextSelectionPopup", (e) => this.selectionPopup(e), id);
  },

  addToWindow(win) {
    win.MozXULElement.insertFTLIfNeeded("aurora.ftl");
    this.windows.add(win);
  },

  removeFromWindow(win) {
    this.windows.delete(win);
  },

  shutdown() {
    if (this.sectionID) Zotero.ItemPaneManager.unregisterSection(this.sectionID);
    if (this.observerID) Zotero.Notifier.unregisterObserver(this.observerID);
  },

  pref(name) {
    return Zotero.Prefs.get("extensions.aurora." + name, true);
  },

  async api(path, { method = "GET", body } = {}) {
    const server = String(this.pref("server") || "http://localhost:8200").replace(/\/$/, "");
    const headers = { "Content-Type": "application/json" };
    const token = this.pref("token");
    if (token) headers.Authorization = "Bearer " + token;
    const xhr = await Zotero.HTTP.request(method, server + "/api" + path, {
      headers, body: body === undefined ? undefined : JSON.stringify(body), responseType: "json",
      successCodes: false, timeout: 120000,
    });
    if (xhr.status < 200 || xhr.status >= 300) {
      const detail = xhr.response && xhr.response.detail;
      throw new Error(detail || `AURORA answered ${xhr.status}`);
    }
    return xhr.response;
  },

  // ------------------------------------------------------------------ which paper an item is

  ids(item) {
    const doi = (item.getField("DOI") || "").trim();
    const url = item.getField("url") || "";
    const extra = item.getField("extra") || "";
    const m = (url + " " + extra + " " + (item.getField("archiveID") || "")).match(/(\d{4}\.\d{4,5})(v\d+)?/);
    return { doi, arxiv: m ? m[1] : "", title: item.getField("title") || "" };
  },

  async pdfBase64(item) {
    try {
      const att = await item.getBestAttachment();
      if (!att || att.attachmentContentType !== "application/pdf") return "";
      const path = await att.getFilePathAsync();
      if (!path) return "";
      const bytes = await IOUtils.read(path);
      if (bytes.length > 40 * 1024 * 1024) return "";
      let bin = "";
      for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      return btoa(bin);
    } catch (e) {
      Zotero.debug("AURORA: reading the PDF failed: " + e);
      return "";
    }
  },

  // ------------------------------------------------------------------ papers you add are read in the background

  onAdded(ids) {
    if (!this.pref("readNew")) return;
    for (const id of ids) {
      const item = Zotero.Items.get(id);
      if (!item || !item.isRegularItem()) continue;
      // the PDF usually arrives a little after the item
      setTimeout(() => this.read(item).catch((e) => Zotero.debug("AURORA: " + e)), 20000);
    }
  },

  async read(item) {
    const ids = this.ids(item);
    if (!ids.doi && !ids.arxiv && !ids.title) return null;
    return this.api("/memory/zotero-read", { method: "POST", body: { ...ids, pdf: await this.pdfBase64(item) } });
  },

  // ------------------------------------------------------------------ the item pane

  async render(body, item) {
    const doc = body.ownerDocument;
    const el = (tag, text, style) => { const e = doc.createElement(tag); if (text) e.textContent = text; if (style) e.setAttribute("style", style); return e; };
    body.textContent = "";
    if (!item || !item.isRegularItem()) { body.append(el("div", "Select a paper.")); return; }
    let view;
    try {
      const ids = this.ids(item);
      view = await this.api(`/memory/zotero-card?doi=${encodeURIComponent(ids.doi)}&arxiv=${encodeURIComponent(ids.arxiv)}&title=${encodeURIComponent(ids.title)}`);
      if (view.state === "none") view = await this.read(item);
    } catch (e) {
      body.append(el("div", String(e.message || e), "color:#b42318"));
      return;
    }
    if (view.state === "pending") {
      body.append(el("div", "Being read in the background. Open it again in a minute.", "color:#667"));
      return;
    }
    if (view.state === "failed") { body.append(el("div", "Could not read it: " + view.error, "color:#b42318")); return; }
    const c = view.card || {};
    if (c.one_line) body.append(el("div", c.one_line, "font-weight:600;margin-bottom:6px"));
    for (const [k, label] of [["methods", "Methods"], ["findings", "Findings"], ["limitations", "Limitations"]]) {
      if (!(c[k] || []).length) continue;
      body.append(el("div", label, "margin-top:8px;font-size:11px;text-transform:uppercase;color:#556"));
      const ul = el("ul", "", "margin:2px 0;padding-left:18px");
      for (const p of c[k]) ul.append(el("li", p.text + (p.label === "inf" ? "  (inferred)" : "")));
      body.append(ul);
    }
    if (c.read_from === "abstract") body.append(el("div", "Read from the abstract only (no PDF).", "color:#667;font-size:11px"));
    if ((view.units || []).length || (view.questions || []).length) {
      body.append(el("div", "Your memory", "margin-top:10px;font-size:11px;text-transform:uppercase;color:#556"));
      if ((view.questions || []).length) body.append(el("div", "Bears on " + view.questions.join(", ")));
      for (const x of view.conflicts || []) body.append(el("div", "Conflicts with what you saved: " + x.other, "color:#b45309"));
      const merged = (view.units || []).filter((u) => u.sources > 1);
      if (merged.length) body.append(el("div", `${merged.length} of its claims you had from other sources too.`, "color:#667"));
    }
    const row = el("div", "", "display:flex;gap:6px;margin-top:10px;flex-wrap:wrap");
    const dissent = el("button", "Sentences your saves argue with");
    dissent.addEventListener("click", () => this.showDissent(body, item, view));
    const note = el("button", "Save the card as a note");
    note.addEventListener("click", () => this.saveNote(item, view, note));
    row.append(dissent, note);
    body.append(row);
  },

  async showDissent(body, item, view) {
    const doc = body.ownerDocument;
    const box = doc.createElement("div");
    box.setAttribute("style", "margin-top:8px");
    box.textContent = "Reading the paper against your memory…";
    body.append(box);
    try {
      const d = await this.api(`/memory/dissent?paper=${encodeURIComponent(view.paper_id)}`);
      box.textContent = d.items.length ? "" : (d.note || "Nothing you saved argues with this paper.");
      if (d.items.length) {
        const lv = ((await this.api("/me/writes")).levels || {})["zotero.annotate"] || "off";
        const row = doc.createElement("div");
        row.setAttribute("style", "display:flex;gap:6px;flex-wrap:wrap");
        if (lv === "off") {
          row.textContent = "To mark them in the PDF, allow “Send the notes you keep on a PDF as Zotero annotations” in AURORA.";
        } else {
          const mark = doc.createElement("button");
          mark.textContent = "Mark them in the PDF";
          mark.addEventListener("click", () => this.markDissent(item, d.items, mark));
          const clear = doc.createElement("button");
          clear.textContent = "Remove the marks";
          clear.addEventListener("click", () => this.clearMarks(item, clear));
          row.append(mark, clear);
          if (lv === "auto") this.markDissent(item, d.items, mark);
        }
        box.append(row);
      }
      for (const it of d.items) {
        const b = doc.createElement("div");
        b.setAttribute("style", "border-left:3px solid #f59e0b;padding:4px 8px;margin:6px 0;cursor:pointer");
        b.textContent = `p. ${it.page + 1}: “${it.quote}” — ${it.why}`;
        b.addEventListener("click", async () => {
          const att = await item.getBestAttachment();
          if (att) Zotero.Reader.open(att.id, { pageIndex: it.page });
        });
        box.append(b);
      }
    } catch (e) {
      box.textContent = String(e.message || e);
    }
  },

  // ------------------------------------------------------------------ marks in the margin (as far as allowed)

  async readerFor(item) {
    const att = await item.getBestAttachment();
    if (!att || !att.isPDFAttachment()) throw new Error("There is no PDF to mark");
    const reader = await Zotero.Reader.open(att.id);
    if (reader && reader._initPromise) await reader._initPromise;
    return { att, reader };
  },

  /** The rectangles of a sentence on a page (PDF coordinates, as Zotero annotations keep them), from pdf.js. */
  async rectsOf(reader, pageIndex, quote) {
    const view = reader._internalReader && reader._internalReader._primaryView;
    const win = view && view._iframeWindow;
    const app = win && (win.wrappedJSObject || win).PDFViewerApplication;
    if (!app || !app.pdfDocument) return [];
    const page = await app.pdfDocument.getPage(pageIndex + 1);
    const items = (await page.getTextContent()).items;
    let text = "";
    const owner = [];
    for (let i = 0; i < items.length; i++) {
      for (const ch of items[i].str) {
        if (/\s/.test(ch)) { if (text && !text.endsWith(" ")) { text += " "; owner.push(i); } }
        else { text += ch.toLowerCase(); owner.push(i); }
      }
      if (!text.endsWith(" ")) { text += " "; owner.push(i); }
    }
    const q = quote.toLowerCase().replace(/\s+/g, " ").trim();
    let at = text.indexOf(q), len = q.length;
    if (at < 0) { at = text.indexOf(q.slice(0, 60)); len = Math.min(q.length, 60); }
    if (at < 0) return [];
    const used = [...new Set(owner.slice(at, at + len))];
    return used.map((i) => {
      const it = items[i], t = it.transform;
      const h = it.height || Math.hypot(t[2], t[3]);
      return [t[4], t[5] - h * 0.2, t[4] + it.width, t[5] + h * 0.85].map((x) => Math.round(x * 1000) / 1000);
    });
  },

  async markDissent(item, list, button) {
    button.disabled = true;
    button.textContent = "Marking…";
    try {
      const { att, reader } = await this.readerFor(item);
      let n = 0;
      for (const it of list) {
        const rects = await this.rectsOf(reader, it.page, it.quote);
        if (!rects.length) continue;
        const claims = it.units.map((u) => `• ${u.claim}${u.sources > 1 ? ` (${u.sources} sources)` : ""}`).join("\n");
        await Zotero.Annotations.saveFromJSON(att, {
          key: Zotero.DataObjectUtilities.generateKey(), type: "highlight", color: "#f59e0b", text: it.quote,
          comment: `AURORA: what you saved ${it.kind} this. ${it.why}\n${claims}`,
          pageLabel: String(it.page + 1), sortIndex: `${String(it.page).padStart(5, "0")}|000000|00000`,
          position: { pageIndex: it.page, rects }, tags: [{ name: "aurora-dissent" }],
        });
        n++;
      }
      if (n) await this.api("/me/writes/log", { method: "POST", body: { write: "zotero.annotate", detail: { item: item.key, marks: n },
        undo: { kind: "zotero_app", how: "Remove the marks from the AURORA section of the item, or delete the annotations tagged aurora-dissent." } } });
      button.textContent = n ? `Marked ${n} in the PDF` : "Could not place them on the page";
    } catch (e) {
      button.disabled = false;
      button.textContent = String(e.message || e).slice(0, 80);
    }
  },

  async clearMarks(item, button) {
    const att = await item.getBestAttachment();
    if (!att) return;
    let n = 0;
    for (const a of att.getAnnotations()) {
      if (a.getTags().some((t) => t.tag === "aurora-dissent")) { await a.eraseTx(); n++; }
    }
    button.textContent = n ? `Removed ${n}` : "No marks to remove";
  },

  async saveNote(item, view, button) {
    try {
      const w = await this.api("/me/writes");
      if ((w.levels || {})["zotero.note"] === "off") {
        button.textContent = "Writing notes is off: allow it in AURORA (account button → What AURORA may write)";
        return;
      }
      const c = view.card || {};
      const list = (k) => (c[k] || []).map((p) => `<li>${Zotero.Utilities.htmlSpecialChars(p.text)}${p.label === "inf" ? " <i>(inferred)</i>" : ""}</li>`).join("");
      const html = `<h2>AURORA: ${Zotero.Utilities.htmlSpecialChars(c.one_line || view.title)}</h2>`
        + `<h3>Methods</h3><ul>${list("methods")}</ul><h3>Findings</h3><ul>${list("findings")}</ul><h3>Limitations</h3><ul>${list("limitations")}</ul>`
        + `<p><i>Written by AURORA from ${c.read_from === "pdf" ? "the PDF" : "the abstract"}.</i></p>`;
      const n = new Zotero.Item("note");
      n.libraryID = item.libraryID;
      n.parentKey = item.key;
      n.setNote(html);
      await n.saveTx();
      await this.api("/me/writes/log", { method: "POST", body: { write: "zotero.note", detail: { item: item.key, note: n.key },
        undo: { kind: "zotero_app", how: "Delete the note in Zotero." } } });
      button.textContent = "Saved as a note";
      button.disabled = true;
    } catch (e) {
      button.textContent = String(e.message || e);
    }
  },

  // ------------------------------------------------------------------ selecting text in the reader

  selectionPopup(event) {
    const { reader, doc, params, append } = event;
    const text = (params && params.annotation && params.annotation.text) || "";
    if (!text) return;
    const item = Zotero.Items.get(reader.itemID);
    const parent = item && item.parentItem;
    const title = parent ? parent.getField("title") : "";
    if (this.pref("reportContext")) {
      this.api("/memory/context", { method: "POST", body: { app: "Zotero", title, selection: text.slice(0, 4000),
        page: String((params.annotation.position || {}).pageIndex + 1 || ""), via: "zotero" } }).catch(() => {});
    }
    const wrap = doc.createElement("div");
    const say = doc.createElement("button");
    say.textContent = "What my saves say";
    const out = doc.createElement("div");
    out.setAttribute("style", "max-width:360px;font-size:12px;margin-top:4px");
    say.addEventListener("click", async () => {
      say.textContent = "Looking…";
      try {
        await this.api("/memory/context", { method: "POST", body: { app: "Zotero", title, selection: text.slice(0, 4000), via: "zotero" } });
        const r = await this.api("/memory/surface?ctx=current");
        const list = [...r.surface, ...r.absorbed].slice(0, 4);
        out.textContent = list.length ? "" : "Nothing you saved bears on it.";
        const STANCE = { challenges: "Challenges it", supports: "Supports it", adds: "Adds to it", background: "Background", tool: "A tool for it" };
        for (const it of list) {
          const d = doc.createElement("div");
          d.setAttribute("style", `border-left:3px solid ${it.stance === "challenges" ? "#f59e0b" : "#2d6cdf"};padding:2px 6px;margin:4px 0`);
          d.textContent = `${STANCE[it.stance] || it.stance}: ${it.claim}${it.sources > 1 ? ` (${it.sources} sources)` : ""}`;
          out.append(d);
        }
        say.textContent = "What my saves say";
      } catch (e) {
        say.textContent = String(e.message || e).slice(0, 60);
      }
    });
    const button = doc.createElement("button");
    button.textContent = "Save to AURORA";
    button.addEventListener("click", async () => {
      button.textContent = "Saving…";
      try {
        const ids = parent ? this.ids(parent) : { doi: "" };
        await this.api("/memory/capture", { method: "POST", body: { text: `${title}\n\n${text}`, selection: text, title,
          url: ids.doi ? `https://doi.org/${ids.doi}` : "", via: "zotero" } });
        button.textContent = "Saved";
      } catch (e) {
        button.textContent = String(e.message || e).slice(0, 60);
      }
    });
    wrap.append(button, say, out);
    append(wrap);
  },
};
