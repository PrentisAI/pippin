"""Skill 4: Simulation Diagnosis (Table 1 — row 4).

Analyses a failed or suspicious DFT / MLIP result and classifies the
failure mode.  Proposes a concrete remediation (retry with better
parameters, fall back to a different method, etc.).  This is the skill
invoked by the just-in-time auditor (Section C.2) when a triage flag
fires.
"""

from __future__ import annotations

from typing import Any

from aurora.skills.base import (
    FailureMode,
    Skill,
    SkillContext,
    SkillInput,
    SkillOutput,
    SkillResult,
    ValidationCheck,
    ValidationResult,
)

# Heuristic thresholds
_SCF_MAX_ITER = 200
_ENERGY_OUTLIER_SIGMA = 4.0
_MLIP_DFT_SIGN_MISMATCH_THRESHOLD = 0.2  # eV — small magnitude with opposite sign


class SimulationDiagnosisSkill(Skill):
    """Diagnose and classify DFT/MLIP simulation failures."""

    name = "simulation_diagnosis"
    description = (
        "Analyse a failed or anomalous simulation result, classify the "
        "failure mode, and propose a remediation."
    )
    required_tools = []  # reads outputs, doesn't run tools

    async def validate_input(
        self, inp: SkillInput, ctx: SkillContext
    ) -> ValidationResult:
        checks: list[ValidationCheck] = []
        p = inp.parameters

        checks.append(
            ValidationCheck(
                name="result_present",
                passed="result" in p or "error_message" in p,
                message="Provide either 'result' (tool output dict) or 'error_message'.",
            )
        )

        return ValidationResult.from_checks(checks)

    async def execute(
        self, inp: SkillInput, ctx: SkillContext
    ) -> SkillResult:
        p = inp.parameters
        result: dict[str, Any] = p.get("result", {})
        error_msg: str = p.get("error_message", "")
        method: str = p.get("method", "dft")  # dft | mlip

        diagnoses: list[dict[str, Any]] = []
        primary_failure: FailureMode = FailureMode.UNKNOWN
        remediation: str = ""

        # ---- Error message analysis ----
        if error_msg:
            lower = error_msg.lower()
            if "scf" in lower or "convergence" in lower or "not converged" in lower:
                diagnoses.append({
                    "mode": FailureMode.SCF_NOT_CONVERGED.value,
                    "confidence": 0.9,
                    "evidence": error_msg,
                })
                primary_failure = FailureMode.SCF_NOT_CONVERGED
                remediation = (
                    "Increase mixing_beta (try 0.3→0.1), add smearing "
                    "(Marzari-Vanderbilt, 0.02 Ry), or improve initial guess."
                )

            elif "walltime" in lower or "timeout" in lower or "time limit" in lower:
                diagnoses.append({
                    "mode": FailureMode.WALLTIME_EXCEEDED.value,
                    "confidence": 0.95,
                    "evidence": error_msg,
                })
                primary_failure = FailureMode.WALLTIME_EXCEEDED
                remediation = (
                    "Request more wall-time, reduce system size, or "
                    "start from a pre-converged wavefunction."
                )

            elif "death" in lower or "loop" in lower or "infinite" in lower:
                diagnoses.append({
                    "mode": FailureMode.SCF_NOT_CONVERGED.value,
                    "confidence": 0.85,
                    "evidence": f"Death-loop detected: {error_msg}",
                })
                primary_failure = FailureMode.SCF_NOT_CONVERGED
                remediation = (
                    "Kill and restart with restart_mode='from_scratch', "
                    "reduce ecutwfc, or switch to a simpler pseudopotential."
                )

        # ---- Numeric output analysis ----
        if result:
            converged = result.get("converged", True)
            if not converged:
                diagnoses.append({
                    "mode": FailureMode.SCF_NOT_CONVERGED.value,
                    "confidence": 0.95,
                    "evidence": "converged=False in output.",
                })
                if primary_failure == FailureMode.UNKNOWN:
                    primary_failure = FailureMode.SCF_NOT_CONVERGED
                    remediation = "Lower mixing_beta and increase max SCF iterations."

            # Check for energy outlier
            energy = result.get("total_energy_eV")
            reference_energies = p.get("reference_energies", [])
            if energy is not None and reference_energies:
                import numpy as np

                ref = np.array(reference_energies, dtype=float)
                mean, std = float(np.mean(ref)), float(np.std(ref))
                if std > 0 and abs(energy - mean) > _ENERGY_OUTLIER_SIGMA * std:
                    diagnoses.append({
                        "mode": FailureMode.ENERGY_OUTLIER.value,
                        "confidence": 0.8,
                        "evidence": (
                            f"E = {energy:.3f} eV is {abs(energy - mean) / std:.1f}σ "
                            f"from reference mean {mean:.3f} ± {std:.3f} eV."
                        ),
                    })
                    if primary_failure == FailureMode.UNKNOWN:
                        primary_failure = FailureMode.ENERGY_OUTLIER
                        remediation = "Re-run with tighter SCF/force thresholds."

            # MLIP-specific: sign-flip detection
            if method == "mlip":
                dft_energy = p.get("dft_reference_energy")
                mlip_energy = result.get("total_energy_eV")
                if dft_energy is not None and mlip_energy is not None:
                    if (
                        abs(mlip_energy) > _MLIP_DFT_SIGN_MISMATCH_THRESHOLD
                        and (dft_energy * mlip_energy) < 0
                    ):
                        diagnoses.append({
                            "mode": FailureMode.MLIP_SIGN_FLIP.value,
                            "confidence": 0.9,
                            "evidence": (
                                f"MLIP energy {mlip_energy:+.3f} eV has opposite sign "
                                f"to DFT reference {dft_energy:+.3f} eV."
                            ),
                        })
                        primary_failure = FailureMode.MLIP_SIGN_FLIP
                        remediation = (
                            "Fall back to DFT for this configuration. "
                            "MLIP models often mis-sign oxygen-bearing intermediates."
                        )

        # ---- Fallback ----
        if not diagnoses:
            diagnoses.append({
                "mode": FailureMode.UNKNOWN.value,
                "confidence": 0.3,
                "evidence": error_msg or "No specific pattern matched.",
            })
            remediation = "Inspect raw output logs and retry with default parameters."

        return SkillResult(
            success=True,
            output=SkillOutput(
                data={
                    "diagnoses": diagnoses,
                    "primary_failure": primary_failure.value,
                    "remediation": remediation,
                    "method": method,
                },
                summary=(
                    f"Diagnosed {primary_failure.value}: {remediation}"
                ),
            ),
            failure_mode=primary_failure,
            remediation_hint=remediation,
        )
