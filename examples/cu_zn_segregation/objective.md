# Cu-Zn Surface Segregation Study

## Research Objective (G₀)

Map the surface segregation landscape of Cu₁₋ₓZnₓ alloys across low-index
facets ((111), (100), (110)) and Zn concentrations (x = 0.05–0.30), quantify
coverage-dependent ΔE_seg, cross-validate DFT with at least two MLIP potentials
(NequIP, CHGNet), and assess implications for methanol synthesis catalyst
stability under operating conditions.

## Context

Cu/ZnO catalysts are among the most important industrial catalysts for methanol
synthesis from syngas (CO₂/CO/H₂).  Surface segregation of Zn to Cu surfaces
under reaction conditions fundamentally affects catalytic activity and selectivity.

Understanding the thermodynamic driving forces for Zn segregation — and how they
depend on crystal facet, Zn concentration, and adsorbate coverage — is essential
for rational catalyst design.

## Computational Approach

1. **DFT calculations** (Quantum ESPRESSO, PBE+D3):
   - Surface slab models for Cu(111), Cu(100), Cu(110)
   - Zn substitution at various concentrations (x = 0.05, 0.10, 0.15, 0.20, 0.25, 0.30)
   - Segregation energy: ΔE_seg = E(Zn@surface) - E(Zn@bulk)
   - CO adsorption on pristine vs Zn-segregated surfaces

2. **MLIP cross-validation**:
   - NequIP and CHGNet potentials
   - Same configurations as DFT
   - ΔΔE_seg = |ΔE_seg(DFT) - ΔE_seg(MLIP)| < 0.5 eV threshold

3. **Analysis**:
   - Coverage-dependent trends: ΔE_seg(x)
   - Facet comparison: (111) vs (100) vs (110)
   - Statistical validation: CV < 0.1 for reliable claims
   - Cluster expansion for interpolation

## Quality Gates (from Paper)
- k-point convergence: < 1 meV/atom
- Energy cutoff convergence: < 1 meV/atom
- ΔΔE_seg > 0.5 eV triggers audit
- CV(ΔE_seg) > 0.1 requires additional sampling
