/* Pippin in Obsidian.
 *
 * - A side view with your memory panel: what surfaces for the note you are writing (the note and your selection
 *   are sent as what you are doing, kept a few hours, never stored; you can turn it off).
 * - Your notes read into your memory (off until you turn it on, and only the folders you name): what they claim is
 *   merged with what you saved elsewhere, and a paper that disagrees with your note says so. Edited notes are read
 *   again; deleted ones leave the memory.
 * - Commands: a literature note from a paper (its methods, findings, limitations and what your memory makes of it),
 *   append an idea unit (claim, sources with their words, conflicts) to the note you are in, save the selection to
 *   Pippin, send this note now.
 *
 * Writing into the vault (creating a note, appending to one) happens only as far as you allowed it in Pippin
 * (account button → What Pippin may write): off gives you the text to copy, "ask" asks each time, "auto" writes.
 * Every write is logged there.
 *
 * Plain CommonJS so there is nothing to build: copy this folder into <vault>/.obsidian/plugins/aurora-memory/.
 */
const { ItemView, Modal, Notice, Plugin, PluginSettingTab, Setting, TFile, MarkdownView, normalizePath, requestUrl } = require("obsidian");

const VIEW = "aurora-memory-view";
const DEFAULTS = {
  server: "http://localhost:8200",
  token: "",
  reportContext: true,
  sendNotes: false,
  folders: "",          // comma-separated; empty: the whole vault
  exclude: "",
  literatureFolder: "Literature",
};

const list = (s) => String(s || "").split(",").map((x) => x.trim().replace(/^\/+|\/+$/g, "")).filter(Boolean);
const safeName = (s) => String(s || "Untitled").replace(/[\\/:*?"<>|#^[\]]/g, " ").replace(/\s+/g, " ").trim().slice(0, 120);

class AuroraView extends ItemView {
  constructor(leaf, plugin) { super(leaf); this.plugin = plugin; }
  getViewType() { return VIEW; }
  getDisplayText() { return "Pippin"; }
  getIcon() { return "sparkles"; }
  async onOpen() { this.draw(); }
  draw() {
    const el = this.containerEl.children[1];
    el.empty();
    el.addClass("aurora-view");
    const s = this.plugin.settings;
    const url = `${s.server.replace(/\/$/, "")}/panel${s.token ? `?token=${encodeURIComponent(s.token)}` : ""}`;
    el.createEl("iframe", { attr: { src: url, title: "Pippin memory" } });
  }
}

class TextModal extends Modal {
  constructor(app, title, text, actions) { super(app); this.title = title; this.text = text; this.actions = actions || []; }
  onOpen() {
    this.modalEl.addClass("aurora-modal");
    this.titleEl.setText(this.title);
    this.contentEl.createEl("pre", { text: this.text });
    const row = this.contentEl.createDiv("aurora-row");
    for (const [label, fn] of this.actions) {
      const b = row.createEl("button", { text: label });
      b.onclick = async () => { await fn(); this.close(); };
    }
    const copy = row.createEl("button", { text: "Copy" });
    copy.onclick = async () => { await navigator.clipboard.writeText(this.text); new Notice("Copied"); };
  }
  onClose() { this.contentEl.empty(); }
}

class AskModal extends Modal {
  constructor(app, title, placeholder, onSubmit) { super(app); this.title = title; this.placeholder = placeholder; this.onSubmit = onSubmit; }
  onOpen() {
    this.modalEl.addClass("aurora-modal");
    this.titleEl.setText(this.title);
    const input = this.contentEl.createEl("input", { type: "text", attr: { placeholder: this.placeholder, style: "width:100%" } });
    this.results = this.contentEl.createDiv();
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && input.value.trim()) this.onSubmit(input.value.trim(), this); });
    input.focus();
  }
  onClose() { this.contentEl.empty(); }
}

class AuroraSettings extends PluginSettingTab {
  constructor(app, plugin) { super(app, plugin); this.plugin = plugin; }
  display() {
    const el = this.containerEl;
    el.empty();
    const p = this.plugin;
    const text = (name, desc, key, secret) => new Setting(el).setName(name).setDesc(desc).addText((t) => {
      if (secret) t.inputEl.type = "password";
      t.setValue(p.settings[key]).onChange(async (v) => { p.settings[key] = v.trim(); await p.saveSettings(); });
    });
    const toggle = (name, desc, key) => new Setting(el).setName(name).setDesc(desc)
      .addToggle((t) => t.setValue(p.settings[key]).onChange(async (v) => { p.settings[key] = v; await p.saveSettings(); }));
    text("Console address", "Where your Pippin console runs, e.g. http://localhost:8200", "server");
    text("Token", "Only when the console has accounts: console → Memory → Browser extension and desktop app → Make a token", "token", true);
    toggle("Tell Pippin what I am writing", "The open note and your selection, so the panel shows what bears on it (kept a few hours, never stored)", "reportContext");
    toggle("Read my notes into my memory", "New and edited notes are sent to Pippin and read; deleted ones leave the memory. Off until you turn it on.", "sendNotes");
    text("Only these folders", "Comma-separated; empty: the whole vault", "folders");
    text("Never these folders", "Comma-separated, e.g. Journal, Private", "exclude");
    text("Literature notes go in", "The folder for literature notes", "literatureFolder");
    new Setting(el).setName("Read the whole vault now").setDesc("Sends every note in the folders above (once; afterwards only changes).")
      .addButton((b) => b.setButtonText("Send all notes").onClick(async () => { const n = await p.sendAll(); new Notice(`Sent ${n} notes to Pippin`); }));
    el.createEl("p", { cls: "setting-item-description",
      text: "Creating or appending notes is set in Pippin (account button → What Pippin may write); it is off until you allow it." });
  }
}

module.exports = class AuroraPlugin extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULTS, await this.loadData());
    this.pending = new Map();
    this.registerView(VIEW, (leaf) => new AuroraView(leaf, this));
    this.addRibbonIcon("sparkles", "Pippin memory", () => this.openView());
    this.addSettingTab(new AuroraSettings(this.app, this));

    this.addCommand({ id: "open-panel", name: "Open the memory panel", callback: () => this.openView() });
    this.addCommand({ id: "literature-note", name: "Literature note from a paper (DOI, arXiv id or title)", callback: () => this.literatureNote() });
    this.addCommand({ id: "append-unit", name: "Append an idea unit from my memory to this note",
      editorCallback: (editor, view) => this.appendUnit(view.file) });
    this.addCommand({ id: "save-selection", name: "Save the selection to Pippin",
      editorCallback: (editor, view) => this.saveSelection(editor.getSelection(), view.file) });
    this.addCommand({ id: "send-note", name: "Send this note to my memory now",
      editorCallback: (editor, view) => this.sendNote(view.file, true) });

    // what you are writing, as context
    this.registerEvent(this.app.workspace.on("active-leaf-change", () => this.reportSoon()));
    this.registerDomEvent(document, "selectionchange", () => this.reportSoon());
    this.registerDomEvent(document, "keyup", () => this.reportSoon());

    // your notes, read into your memory
    this.registerEvent(this.app.vault.on("modify", (f) => this.noteChanged(f)));
    this.registerEvent(this.app.vault.on("create", (f) => this.noteChanged(f)));
    this.registerEvent(this.app.vault.on("delete", (f) => this.noteGone(f.path)));
    this.registerEvent(this.app.vault.on("rename", (f, old) => { this.noteGone(old); this.noteChanged(f); }));
  }

  onunload() { for (const t of this.pending.values()) clearTimeout(t); }

  async saveSettings() {
    await this.saveData(this.settings);
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW)) leaf.view.draw && leaf.view.draw();
  }

  async openView() {
    let leaf = this.app.workspace.getLeavesOfType(VIEW)[0];
    if (!leaf) { leaf = this.app.workspace.getRightLeaf(false); await leaf.setViewState({ type: VIEW, active: true }); }
    this.app.workspace.revealLeaf(leaf);
  }

  async api(path, { method = "GET", body } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (this.settings.token) headers.Authorization = `Bearer ${this.settings.token}`;
    const r = await requestUrl({ url: `${this.settings.server.replace(/\/$/, "")}/api${path}`, method, headers,
      body: body === undefined ? undefined : JSON.stringify(body), throw: false });
    if (r.status < 200 || r.status >= 300) {
      let detail = "";
      try { detail = r.json.detail; } catch (e) { detail = ""; }
      throw new Error(detail || `Pippin answered ${r.status}`);
    }
    return r.json;
  }

  // ------------------------------------------------------------------ context

  reportSoon() {
    if (!this.settings.reportContext) return;
    clearTimeout(this.ctxTimer);
    this.ctxTimer = setTimeout(() => this.report().catch(() => {}), 4000);
  }

  async report() {
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (!view || !view.file) return;
    const editor = view.editor;
    const selection = editor.getSelection();
    const line = editor.getCursor().line;
    const around = [];
    for (let i = Math.max(0, line - 6); i <= Math.min(editor.lineCount() - 1, line + 6); i++) around.push(editor.getLine(i));
    const key = `${view.file.path}|${selection}|${line}`;
    if (key === this.lastCtx) return;
    this.lastCtx = key;
    await this.api("/memory/context", { method: "POST", body: { app: "Obsidian", title: view.file.basename,
      selection: selection.slice(0, 4000), text: around.join("\n").slice(0, 4000), via: "obsidian" } });
  }

  // ------------------------------------------------------------------ your notes into your memory

  wanted(path) {
    if (!path.endsWith(".md")) return false;
    if (path.split("/").some((p) => p.startsWith("."))) return false;
    const folders = list(this.settings.folders), exclude = list(this.settings.exclude);
    if (exclude.some((f) => path === f || path.startsWith(f + "/"))) return false;
    return !folders.length || folders.some((f) => path.startsWith(f + "/"));
  }

  noteChanged(f) {
    if (!this.settings.sendNotes || !(f instanceof TFile) || !this.wanted(f.path)) return;
    clearTimeout(this.pending.get(f.path));
    // wait until you stop editing it for a while
    this.pending.set(f.path, setTimeout(() => { this.pending.delete(f.path); this.sendNote(f, false).catch(() => {}); }, 60000));
  }

  noteGone(path) {
    if (!this.settings.sendNotes || !path.endsWith(".md")) return;
    clearTimeout(this.pending.get(path));
    const vault = this.app.vault.getName();
    this.api(`/memory/notes?vault_name=${encodeURIComponent(vault)}&path=${encodeURIComponent(path)}`, { method: "DELETE" }).catch(() => {});
  }

  async sendNote(f, loud) {
    if (!f) return;
    try {
      const text = await this.app.vault.cachedRead(f);
      const r = await this.api("/memory/notes", { method: "POST", body: { vault: this.app.vault.getName(), path: f.path, text, mtime: f.stat.mtime / 1000 } });
      if (loud) new Notice(r.state === "too_short" ? "The note is too short to read" : r.state === "unchanged" ? "Pippin already has this version" : "Sent: Pippin is reading it");
    } catch (e) {
      if (loud) new Notice(`Pippin: ${e.message}`);
    }
  }

  async sendAll() {
    let n = 0;
    for (const f of this.app.vault.getMarkdownFiles()) {
      if (!this.wanted(f.path)) continue;
      await this.sendNote(f, false);
      n++;
    }
    return n;
  }

  // ------------------------------------------------------------------ writing into the vault, as far as allowed

  async level(write) {
    try { return ((await this.api("/me/writes")).levels || {})[write] || "off"; } catch (e) { return "off"; }
  }

  async logWrite(write, detail, how) {
    await this.api("/me/writes/log", { method: "POST", body: { write, detail, undo: { kind: "obsidian", how } } }).catch(() => {});
  }

  /** Run `doWrite` when the write is allowed (asking first when it is set to ask); otherwise show the text to copy. */
  async gated(write, title, text, doWrite) {
    const lv = await this.level(write);
    if (lv === "auto") return doWrite();
    if (lv === "ask") return new TextModal(this.app, title, text, [["Write it", doWrite]]).open();
    new TextModal(this.app, `${title} (writing is off in Pippin: copy it)`, text).open();
  }

  literatureNote() {
    new AskModal(this.app, "Literature note", "DOI, arXiv id or title", async (q, modal) => {
      modal.results.setText("Pippin is reading the paper…");
      const doi = /^10\.\d{4,9}\//.test(q) ? q : "";
      const arxiv = /^\d{4}\.\d{4,5}(v\d+)?$/.test(q) ? q.replace(/v\d+$/, "") : "";
      const qs = `doi=${encodeURIComponent(doi)}&arxiv=${encodeURIComponent(arxiv)}&title=${encodeURIComponent(doi || arxiv ? "" : q)}`;
      try {
        let v = await this.api(`/memory/zotero-card?${qs}`);
        if (v.state === "none" || v.state === "failed") v = await this.api("/memory/zotero-read", { method: "POST", body: { doi, arxiv, title: doi || arxiv ? "" : q } });
        for (let i = 0; i < 60 && v.state === "pending"; i++) {
          await new Promise((r) => setTimeout(r, 4000));
          v = await this.api(`/memory/zotero-card?${qs}`);
        }
        if (v.state !== "done") throw new Error(v.error || "Pippin is still reading it; try again in a minute");
        modal.close();
        const md = this.literatureMarkdown(v);
        const path = normalizePath(`${this.settings.literatureFolder}/${safeName(v.title)}.md`);
        await this.gated("obsidian.create", `Literature note: ${v.title}`, md, async () => {
          if (this.app.vault.getAbstractFileByPath(path)) { new Notice(`${path} already exists`); return; }
          const folder = this.settings.literatureFolder;
          if (folder && !this.app.vault.getAbstractFileByPath(folder)) await this.app.vault.createFolder(folder);
          const f = await this.app.vault.create(path, md);
          await this.logWrite("obsidian.create", { path }, `Delete the note ${path} in Obsidian.`);
          this.app.workspace.getLeaf(false).openFile(f);
        });
      } catch (e) {
        modal.results.setText(e.message);
      }
    }).open();
  }

  literatureMarkdown(v) {
    const c = v.card || {};
    const pts = (k) => (c[k] || []).map((p) => `- ${p.text}${p.label === "inf" ? " *(inferred)*" : ""}`).join("\n") || "- (none)";
    const out = ["---", `title: "${String(v.title).replace(/"/g, "'")}"`, `aurora: ${v.paper_id}`, "tags: [literature]", "---", "",
      `# ${v.title}`, "", c.one_line || "", "", "## Methods", pts("methods"), "", "## Findings", pts("findings"), "",
      "## Limitations", pts("limitations"), ""];
    if ((v.questions || []).length || (v.conflicts || []).length) {
      out.push("## In my memory");
      if ((v.questions || []).length) out.push(`- Bears on ${v.questions.join(", ")}`);
      for (const x of v.conflicts || []) out.push(`- Conflicts with what I saved: ${x.other}`);
      out.push("");
    }
    out.push(`*Read by Pippin from ${c.read_from === "pdf" ? "the PDF" : "the abstract"}.*`, "", "## My notes", "");
    return out.join("\n");
  }

  appendUnit(file) {
    if (!file) return;
    new AskModal(this.app, "Append an idea unit", "Search your memory", async (q, modal) => {
      modal.results.setText("Searching…");
      try {
        const hits = await this.api(`/memory/search?q=${encodeURIComponent(q)}`);
        modal.results.empty();
        if (!hits.length) modal.results.setText("Nothing in your memory matches.");
        for (const h of hits) {
          const d = modal.results.createDiv("aurora-result");
          d.createDiv({ text: h.claim });
          d.createDiv({ cls: "aurora-sub", text: `${h.sources} source${h.sources === 1 ? "" : "s"}` });
          d.onclick = async () => {
            modal.close();
            const md = (await this.api(`/memory/units/${h.unit_id}/markdown`)).markdown;
            await this.gated("obsidian.append", `Append to ${file.basename}`, md, async () => {
              await this.app.vault.append(file, "\n" + md);
              await this.logWrite("obsidian.append", { path: file.path, unit: h.unit_id }, `Remove the appended passage from ${file.path} (Obsidian's file recovery has the earlier version).`);
              new Notice("Appended");
            });
          };
        }
      } catch (e) {
        modal.results.setText(e.message);
      }
    }).open();
  }

  async saveSelection(text, file) {
    if (!text.trim()) { new Notice("Select the passage to save first"); return; }
    try {
      await this.api("/memory/capture", { method: "POST", body: { text, selection: text, title: file ? file.basename : "", via: "obsidian" } });
      new Notice("Saved: Pippin is reading it");
    } catch (e) {
      new Notice(`Pippin: ${e.message}`);
    }
  }
};
