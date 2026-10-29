"""The skill catalog: skills others have written and shared, to browse and add to the library.

Like an editor's extensions: Pippin lists them by category, each with where it comes from (the repository, its
licence, the commit it is pinned to) and a few words on what it is for; the researcher chooses which to add.  An
added skill is the whole folder of its source (SKILL.md with its references and scripts), downloaded at that
commit with the licence beside it, and kept in the library like any other skill (``origin="catalog"``): it has a
scope, can be removed, and is written where the agents' Claude Code finds it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from aurora.skills.store import DistilledSkill, SkillStore


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    repo: str              # owner/name on GitHub
    commit: str            # what is downloaded: pinned, so a skill is what was looked at
    license: str
    license_file: str      # at the repository's root, copied beside each skill
    about: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}"


@dataclass(frozen=True)
class Entry:
    name: str              # the skill's own name (its folder, and its SKILL.md's name)
    source: str
    path: str              # its folder in the repository
    category: str
    summary: str           # what it is for, in a few words (ours, not the author's)
    recommended: bool = False
    needs: str = ""        # what it needs beyond Python (a key, a service)


SOURCES = {s.id: s for s in [
    Source("k-dense", "Scientific Agent Skills (K-Dense)", "K-Dense-AI/scientific-agent-skills",
           "5f2bc0341354fd5590aef76a533ada1e1e5b4e62", "MIT", "LICENSE.md",
           "The most used open library of science skills: research methods, statistics and 100+ domain tools."),
    Source("nature", "Nature Skills", "Yuan1z0825/nature-skills",
           "4a04b953c13c57adc51e202ce296597f00d29d67", "Apache-2.0", "LICENSE",
           "Skills for writing, reviewing and checking manuscripts, to the standard of Nature-family journals."),
    Source("orchestra", "AI Research Skills (Orchestra)", "Orchestra-Research/AI-Research-SKILLs",
           "773a52944ba4747a18bd4ae9ade53fff041adcbc", "MIT", "LICENSE",
           "Skills for ML research: training and evaluation tools, ideation, and writing ML papers."),
]}

CATEGORIES = [
    ("thinking", "Research thinking and design"),
    ("literature", "Literature and citations"),
    ("stats", "Statistics and data analysis"),
    ("writing", "Writing and review"),
    ("figures", "Figures and talks"),
    ("materials", "Materials and chemistry"),
    ("life", "Life sciences"),
    ("physics", "Physics, maths and computing"),
    ("ml", "Machine learning research"),
]

_K, _N, _O = "k-dense", "nature", "orchestra"
ENTRIES = [
    # research thinking and design
    Entry("scientific-critical-thinking", _K, "skills/scientific-critical-thinking", "thinking",
          "Appraise a claim and its evidence: design validity, biases and confounders, how much the statistics "
          "support, evidence grading (GRADE, risk of bias).", recommended=True),
    Entry("hypothesis-generation", _K, "skills/hypothesis-generation", "thinking",
          "Turn an observation into testable hypotheses with rival explanations, the predictions that tell them "
          "apart, and an analysis plan fixed in advance.", recommended=True),
    Entry("experimental-design", _K, "skills/experimental-design", "thinking",
          "Plan a study before the data: choose the design, randomise and block, lay out the runs so the result "
          "can be interpreted. Includes design generators.", recommended=True),
    Entry("scientific-brainstorming", _K, "skills/scientific-brainstorming", "thinking",
          "Ideation by one agent with independent ideas, stated assumptions, adversarial review and a decision "
          "log. (Pippin's round table does this across several models.)"),
    Entry("what-if-oracle", _K, "skills/what-if-oracle", "thinking",
          "Scenario analysis for a research plan: favourable, adverse, wild-card and second-order outcomes, and "
          "what to do in each."),
    Entry("brainstorming-research-ideas", _O, "21-research-ideation/brainstorming-research-ideas", "thinking",
          "Structured frameworks for finding high-impact research directions when starting or pivoting."),
    Entry("creative-thinking-for-research", _O, "21-research-ideation/creative-thinking-for-research", "thinking",
          "Ways to reach genuinely new directions: analogy, recombination, changing the constraints."),
    # literature and citations
    Entry("literature-review", _K, "skills/literature-review", "literature",
          "Systematic, scoping or narrative reviews across PubMed, arXiv, bioRxiv and Semantic Scholar, with "
          "screening and PRISMA reporting.", recommended=True),
    Entry("citation-management", _K, "skills/citation-management", "literature",
          "Find papers in OpenAlex, PubMed and Google Scholar, check citations against their records, and write "
          "correct BibTeX.", recommended=True),
    Entry("paper-lookup", _K, "skills/paper-lookup", "literature",
          "Search 18 scholarly APIs for papers, preprints, citations and open-access full text, recording where "
          "each result came from."),
    Entry("nature-ref-verifier", _N, "skills/nature-ref-verifier", "literature",
          "Check a reference list field by field against several sources: authors, title, year, volume, pages, "
          "DOI. Flags mismatches. (Instructions in Chinese.)"),
    Entry("nature-paper-card", _N, "skills/nature-paper-card", "literature",
          "A structured close reading of one paper: methods, which experiment supports which claim, limits, and "
          "ideas it suggests.", recommended=True),
    Entry("nature-paper-xray", _N, "skills/nature-paper-xray", "literature",
          "Read one paper closely enough to reconstruct how the authors got there and why they chose their "
          "design, not just what the abstract says."),
    Entry("nature-academic-search", _N, "skills/nature-academic-search", "literature",
          "Literature search across sources, citation checks, MeSH search strategies and citation-impact audits."),
    # statistics and data analysis
    Entry("statistical-analysis", _K, "skills/statistical-analysis", "stats",
          "Choose the right test, check its assumptions, report effect sizes and power, with Bayesian "
          "alternatives.", recommended=True),
    Entry("statistical-power", _K, "skills/statistical-power", "stats",
          "How many samples or replicates are needed: a priori power, the smallest detectable effect, power "
          "curves, by formula or simulation.", recommended=True),
    Entry("uncertainty-and-units", _K, "skills/uncertainty-and-units", "stats",
          "Carry units and measurement uncertainty through a calculation: dimensional checks, uncertainty "
          "budgets (GUM), propagation.", recommended=True),
    Entry("exploratory-data-analysis", _K, "skills/exploratory-data-analysis", "stats",
          "A first look at a data file: profile, missing values, leakage, outliers, before any modelling."),
    Entry("nature-statistics", _N, "skills/nature-statistics", "stats",
          "Audit a manuscript's statistical reporting: experimental units, replication, uncertainty, tests, "
          "and the statistics in figure legends.", recommended=True),
    Entry("pymc", _K, "skills/pymc", "stats",
          "Bayesian models with PyMC: hierarchical models, sampling, diagnostics, posterior checks, model "
          "comparison."),
    Entry("statsmodels", _K, "skills/statsmodels", "stats",
          "Regression and time series with statsmodels: OLS, GLM, mixed models, ARIMA, with diagnostics."),
    Entry("scikit-learn", _K, "skills/scikit-learn", "stats",
          "Classical machine learning with scikit-learn: models, validation, tuning, pipelines."),
    Entry("shap", _K, "skills/shap", "stats",
          "Explain a model's predictions with SHAP values, and check the explanations hold."),
    # writing and review
    Entry("peer-review", _K, "skills/peer-review", "writing",
          "Draft a constructive, evidence-bounded review of a manuscript, preprint or proposal, following the "
          "relevant reporting guideline.", recommended=True),
    Entry("nature-reviewer", _N, "skills/nature-reviewer", "writing",
          "A mock peer review before submission: significance, validity, major and minor concerns, as a "
          "Nature-family reviewer would.", recommended=True),
    Entry("scientific-writing", _K, "skills/scientific-writing", "writing",
          "Draft, revise and audit a manuscript, keeping each statement traceable to its evidence and the "
          "reporting guideline covered.", recommended=True),
    Entry("nature-writing", _N, "skills/nature-writing", "writing",
          "Draft or restructure a manuscript's argument and sections from the authors' own evidence."),
    Entry("nature-polishing", _N, "skills/nature-polishing", "writing",
          "Polish or tighten existing academic prose, and translate it, without changing facts or claims."),
    Entry("nature-response", _N, "skills/nature-response", "writing",
          "Answer reviewers point by point, write the revision letter, and prepare the marked-up manuscript."),
    Entry("nature-data", _N, "skills/nature-data", "writing",
          "Write or check the Data and Code Availability statements: repositories, access, FAIR metadata."),
    Entry("nature-proposal-writer", _N, "skills/nature-proposal-writer", "writing",
          "Write or review a research proposal or research plan from the evidence behind it."),
    Entry("research-grants", _K, "skills/research-grants", "writing",
          "Grant proposals for NSF, NIH, DOE and DARPA: aims, review criteria, budgets, resubmissions."),
    Entry("venue-templates", _K, "skills/venue-templates", "writing",
          "Format a paper, poster or grant for a given venue with its official LaTeX template; checks page "
          "limits and anonymity."),
    Entry("ml-paper-writing", _O, "20-ml-paper-writing/ml-paper-writing", "writing",
          "Write ML papers for NeurIPS, ICML, ICLR, ACL and others: structure, argument, citations, camera-ready."),
    Entry("systems-paper-writing", _O, "20-ml-paper-writing/systems-paper-writing", "writing",
          "Write systems papers for OSDI, SOSP, NSDI and EuroSys: structure, venue checklists, what reviewers "
          "look for."),
    # figures and talks
    Entry("scientific-visualization", _K, "skills/scientific-visualization", "figures",
          "Truthful, accessible, publication-ready figures: multi-panel layouts, uncertainty, colour and "
          "contrast checks.", recommended=True),
    Entry("matplotlib", _K, "skills/matplotlib", "figures", "Fine control of Matplotlib plots, exported for print."),
    Entry("seaborn", _K, "skills/seaborn", "figures", "Statistical plots with Seaborn from pandas data."),
    Entry("academic-plotting", _O, "20-ml-paper-writing/academic-plotting", "figures",
          "Figures for ML papers: result plots, and architecture diagrams drawn from the paper's text.",
          needs="A Gemini API key for the diagrams"),
    Entry("scientific-slides", _K, "skills/scientific-slides", "figures",
          "Slides for a research talk: structure, figures, and what to say."),
    Entry("latex-posters", _K, "skills/latex-posters", "figures",
          "Conference posters in LaTeX (beamerposter, tikzposter), with a check before printing."),
    # materials and chemistry
    Entry("pymatgen", _K, "skills/pymatgen", "materials",
          "Materials structures and computed data with pymatgen: symmetry, phase diagrams, VASP input and "
          "output, Materials Project queries."),
    Entry("pycalphad", _K, "skills/pycalphad", "materials",
          "Phase equilibria of alloys from CALPHAD databases: phase fractions and compositions with "
          "temperature."),
    Entry("molecular-dynamics", _K, "skills/molecular-dynamics", "materials",
          "Run and analyse molecular dynamics with OpenMM and MDAnalysis."),
    Entry("cantera", _K, "skills/cantera", "materials",
          "Chemical kinetics with Cantera: reactors, ignition delay, with conservation checks."),
    Entry("pybamm", _K, "skills/pybamm", "materials",
          "Simulate lithium-ion battery cycling with PyBaMM and compare with measured curves."),
    Entry("rdkit", _K, "skills/rdkit", "materials",
          "Cheminformatics with RDKit: parse molecules, descriptors, fingerprints, substructures, similarity."),
    Entry("datamol", _K, "skills/datamol", "materials", "A simpler interface to RDKit for everyday molecule work."),
    Entry("deepchem", _K, "skills/deepchem", "materials",
          "Predict molecular properties with DeepChem, with scaffold splits so results generalise."),
    # life sciences
    Entry("biopython", _K, "skills/biopython", "life",
          "Sequences, file formats, phylogenetics and NCBI access with Biopython."),
    Entry("scanpy", _K, "skills/scanpy", "life",
          "Single-cell RNA-seq with Scanpy: quality control, normalisation, clustering, markers."),
    Entry("bulk-rnaseq", _K, "skills/bulk-rnaseq", "life",
          "Prepare bulk RNA-seq quantification for differential expression, with replication and strandedness "
          "checks."),
    Entry("pydeseq2", _K, "skills/pydeseq2", "life", "Differential expression with PyDESeq2: designs, contrasts, FDR."),
    Entry("esm", _K, "skills/esm", "life", "Protein language models (ESM3, ESMC, ESMFold) for generation, embeddings, folding."),
    # physics, maths and computing
    Entry("sympy", _K, "skills/sympy", "physics", "Exact symbolic maths with SymPy: algebra, calculus, solving, code generation."),
    Entry("astropy", _K, "skills/astropy", "physics", "Astronomy with Astropy: units, coordinates, FITS files, time, cosmology."),
    Entry("qutip", _K, "skills/qutip", "physics", "Quantum dynamics of closed and open systems with QuTiP."),
    Entry("fluidsim", _K, "skills/fluidsim", "physics",
          "Computational fluid dynamics with FluidSim, with numerical-validity checks."),
    Entry("networkx", _K, "skills/networkx", "physics", "Graphs and networks with NetworkX: paths, centrality, communities."),
    Entry("optimize-for-gpu", _K, "skills/optimize-for-gpu", "physics",
          "Move scientific Python to the GPU and check the result is both correct and faster."),
    Entry("dask", _K, "skills/dask", "physics", "Data larger than memory, or across a cluster, with Dask."),
    Entry("polars", _K, "skills/polars", "physics", "Fast data frames with Polars, including data larger than memory."),
    # machine learning research
    Entry("ml-training-recipes", _O, "10-optimization/ml-training-recipes", "ml",
          "Proven PyTorch training recipes: loops, optimisers, learning-rate schedules, mixed precision."),
    Entry("lm-evaluation-harness", _O, "11-evaluation/lm-evaluation-harness", "ml",
          "Benchmark language models on 60+ standard tasks with the LM Evaluation Harness."),
    Entry("trl-fine-tuning", _O, "06-post-training/trl-fine-tuning", "ml",
          "Fine-tune language models with TRL: SFT, DPO, PPO and GRPO."),
    Entry("peft", _O, "03-fine-tuning/peft", "ml", "Parameter-efficient fine-tuning (LoRA, QLoRA) with PEFT."),
    Entry("vllm", _O, "12-inference-serving/vllm", "ml", "Serve and run language models fast with vLLM."),
    Entry("transformer-lens", _O, "04-mechanistic-interpretability/transformer-lens", "ml",
          "Mechanistic interpretability with TransformerLens: hooks, activation caching, ablations."),
    Entry("weights-and-biases", _O, "13-mlops/weights-and-biases", "ml",
          "Track runs, sweeps and models with Weights & Biases.", needs="A W&B account"),
]
_BY_NAME = {e.name: e for e in ENTRIES}

MAX_BYTES = 20_000_000            # one skill's folder; larger ones are not offered


def client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "Pippin skill catalog"})


class CatalogError(Exception):
    pass


def _home() -> Path:
    from aurora.paths import aurora_home
    d = aurora_home() / "skill_catalog"
    d.mkdir(parents=True, exist_ok=True)
    return d


def listing(store: SkillStore) -> dict[str, Any]:
    """The catalog, with what is already in the library."""
    added = {s.source.get("catalog"): s for s in store.list_all() if s.origin == "catalog" and s.source.get("catalog")}
    taken = {s.name for s in store.list_all()}
    entries = []
    for e in ENTRIES:
        src = SOURCES[e.source]
        mine = added.get(e.name)
        entries.append({**asdict(e), "source_name": src.name, "repo": src.repo, "license": src.license,
                        "url": f"{src.url}/tree/{src.commit}/{e.path}",
                        "skill_id": mine.skill_id if mine else "",
                        "scope": mine.scope if mine else "",
                        "name_taken": not mine and e.name in taken})
    return {"categories": [{"id": c, "label": label} for c, label in CATEGORIES],
            "sources": [{**asdict(s), "url": s.url} for s in SOURCES.values()],
            "entries": entries}


async def _tree(src: Source, client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """The files of the repository at its pinned commit (one request, then kept)."""
    cache = _home() / "trees" / f"{src.id}-{src.commit}.json"
    if cache.is_file():
        return json.loads(cache.read_text())
    r = await client.get(f"https://api.github.com/repos/{src.repo}/git/trees/{src.commit}", params={"recursive": "1"})
    if r.status_code != 200:
        raise CatalogError(f"GitHub did not list {src.repo} ({r.status_code}); try again in a while")
    tree = [{"path": t["path"], "size": t.get("size", 0)} for t in r.json().get("tree", []) if t.get("type") == "blob"]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(tree))
    return tree


async def install(name: str, store: SkillStore, *, scope: str = "global", projects: list[str] | None = None,
                  client: httpx.AsyncClient | None = None, **gate: str) -> DistilledSkill:
    """Download a catalog skill's folder at its pinned commit and add it to the library."""
    entry = _BY_NAME.get(name)
    if entry is None:
        raise CatalogError(f"No skill “{name}” in the catalog")
    if existing := store.get_by_name(entry.name):
        if existing.origin == "catalog" and existing.source.get("catalog") == entry.name:
            return existing
        raise CatalogError(f"The library already has a skill named “{entry.name}”: rename or remove it first")
    src = SOURCES[entry.source]
    own = client is None
    client = client or client_factory()
    try:
        tree = await _tree(src, client)
        prefix = entry.path.rstrip("/") + "/"
        files = [t for t in tree if t["path"].startswith(prefix)]
        if not any(t["path"] == prefix + "SKILL.md" for t in files):
            raise CatalogError(f"{src.repo} has no {prefix}SKILL.md at the pinned commit")
        if sum(t["size"] for t in files) > MAX_BYTES:
            raise CatalogError(f"“{entry.name}” is larger than {MAX_BYTES // 1_000_000} MB")
        dest = _home() / "skills" / entry.name
        tmp = dest.with_name(dest.name + ".part")
        shutil.rmtree(tmp, ignore_errors=True)
        raw = f"https://raw.githubusercontent.com/{src.repo}/{src.commit}/"
        sem = asyncio.Semaphore(8)

        async def fetch(rel: str, to: Path) -> None:
            async with sem:
                r = await client.get(raw + rel)
            if r.status_code != 200:
                raise CatalogError(f"Could not download {rel} ({r.status_code})")
            to.parent.mkdir(parents=True, exist_ok=True)
            to.write_bytes(r.content)

        jobs = []
        for t in files:
            rel = t["path"][len(prefix):]
            if not rel or ".." in Path(rel).parts or rel.startswith("/"):
                continue
            jobs.append(fetch(t["path"], tmp / rel))
        jobs.append(fetch(src.license_file, tmp / "LICENSE"))
        try:
            await asyncio.gather(*jobs)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    finally:
        if own:
            await client.aclose()
    where = f"{src.url}/tree/{src.commit}/{entry.path}"
    (tmp / "SOURCE.md").write_text(
        f"# Where this skill comes from\n\n“{entry.name}” is from {src.name} ({src.url}), {entry.path} at commit "
        f"{src.commit}, under the {src.license} licence (LICENSE, beside this file).\n\n{where}\n\n"
        "Added to Pippin from its skill catalog; it is the authors' work, unchanged.\n")
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    text = (dest / "SKILL.md").read_text(errors="replace")
    skill = DistilledSkill(
        name=entry.name, rule=text, description=entry.summary, scope=scope, projects=projects or [],
        tags=[entry.category], origin="catalog", kind="procedure",
        source={"catalog": entry.name, "source": src.id, "source_name": src.name, "repo": src.repo,
                "commit": src.commit, "path": entry.path, "license": src.license, "url": where,
                "bundle": str(dest)},
        **gate)
    return store.create(skill)


def remove_bundle(skill: DistilledSkill) -> None:
    """Delete an added skill's downloaded folder (only ever one inside the catalog's own folder)."""
    bundle = Path(skill.source.get("bundle") or "")
    home = _home() / "skills"
    if bundle.name and bundle.parent == home and bundle.is_dir():
        shutil.rmtree(bundle, ignore_errors=True)


def bundle_of(skill: DistilledSkill) -> Path | None:
    """The folder to put where the agents find it, for a skill added from the catalog."""
    p = Path(skill.source.get("bundle") or "")
    return p if skill.origin == "catalog" and p.name and (p / "SKILL.md").is_file() else None
