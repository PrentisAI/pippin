"""Convert AURORA tools into Claude API tool schemas for agentic loops.

Provides two categories of tools:

1. **Computational tools** — wrappers around the existing ToolRegistry
   (ASE, DFT, MLIP, HPC, literature, analysis, visualization).
2. **Meta-tools** — agent-level actions that were previously engine-mediated:
   record_observation, record_claim, read_artifacts, decide_next_step,
   request_audit, run_analysis_script.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aurora.llm.tool_runner import ToolSchema
from aurora.models.artifacts import Observation, ResearchArtifacts
from aurora.state.artifacts import ArtifactStore
from aurora.tools.base import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Computational tool schema builder
# ---------------------------------------------------------------------------

# Input schemas for each known tool (Claude API format)
_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "mock_dft": {
        "type": "object",
        "properties": {
            "structure": {
                "type": "object",
                "description": "Atomic structure: {atoms: [str], facet: str, dopant_site: str, adsorbate: str|null}",
            },
            "kpoints": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "K-point mesh as [kx, ky, kz]",
            },
            "ecutwfc": {"type": "number", "description": "Plane-wave cutoff energy (Ry)"},
            "ecutrho": {"type": "number", "description": "Charge density cutoff (Ry)"},
            "calculation": {
                "type": "string",
                "enum": ["scf", "relax", "vc-relax"],
                "description": "Type of DFT calculation",
            },
            "max_steps": {"type": "integer", "description": "Max optimization steps (for relax)"},
        },
        "required": ["structure", "kpoints", "ecutwfc", "ecutrho", "calculation"],
    },
    "dft": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["setup_calculation", "run_calculation", "parse_output", "check_convergence"],
            },
            "structure_file": {"type": "string"},
            "functional": {"type": "string", "enum": ["PBE", "PBEsol", "SCAN"]},
            "ecutwfc": {"type": "number"},
            "ecutrho": {"type": "number"},
            "kpoints": {"type": "array", "items": {"type": "integer"}},
            "calculation": {"type": "string", "enum": ["scf", "relax", "vc-relax"]},
            "output_dir": {"type": "string"},
            "input_file": {"type": "string"},
            "output_file": {"type": "string"},
        },
        "required": ["operation"],
    },
    "ase": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["build_slab", "build_bulk", "add_adsorbate", "add_dopant", "write_structure", "read_structure"],
            },
            "element": {"type": "string"},
            "facet": {"type": "string"},
            "size": {"type": "array", "items": {"type": "integer"}},
            "vacuum": {"type": "number"},
            "a": {"type": "number"},
            "crystal_structure": {"type": "string"},
            "adsorbate": {"type": "string"},
            "height": {"type": "number"},
            "position": {"type": "string"},
            "input_file": {"type": "string"},
            "dopant_element": {"type": "string"},
            "site_index": {"type": "integer"},
            "output_file": {"type": "string"},
            "format": {"type": "string"},
        },
        "required": ["operation"],
    },
    "mock_ase": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["build_slab", "build_bulk", "add_adsorbate", "add_dopant", "optimize_geometry"],
            },
            "element": {"type": "string"},
            "facet": {"type": "string"},
            "size": {"type": "array", "items": {"type": "integer"}},
            "vacuum": {"type": "number"},
            "adsorbate": {"type": "string"},
            "dopant": {"type": "string"},
            "site": {"type": "string"},
        },
        "required": ["operation"],
    },
    "mock_mlip": {
        "type": "object",
        "properties": {
            "structure": {
                "type": "object",
                "description": "Atomic structure: {atoms: [str], facet: str}",
            },
            "model_name": {
                "type": "string",
                "enum": ["mace_mp0", "chgnet_v030", "nequip_custom"],
                "description": "MLIP model to use",
            },
            "task": {
                "type": "string",
                "enum": ["single_point", "relax", "md"],
                "description": "Computation task type",
            },
        },
        "required": ["structure", "model_name", "task"],
    },
    "mlip": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["evaluate", "relax"],
            },
            "structure_file": {"type": "string"},
            "model_name": {"type": "string"},
            "fmax": {"type": "number"},
            "max_steps": {"type": "integer"},
        },
        "required": ["operation", "structure_file", "model_name"],
    },
    "analysis": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": "Analysis operation to perform",
            },
            "data": {
                "type": "object",
                "description": "Input data for analysis",
            },
        },
        "required": ["operation"],
    },
    "visualization": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": "Visualization operation",
            },
            "data": {
                "type": "object",
                "description": "Data to visualize",
            },
            "output_path": {"type": "string"},
        },
        "required": ["operation"],
    },
    "literature": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer"},
            "source": {"type": "string", "enum": ["arxiv", "semantic_scholar", "both"]},
        },
        "required": ["query"],
    },
    "hpc": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["submit_job", "check_status", "cancel_job", "wait_for_completion"],
            },
            "script_path": {"type": "string"},
            "job_id": {"type": "string"},
            "timeout": {"type": "number"},
        },
        "required": ["operation"],
    },
}

_FALLBACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "parameters": {
            "type": "object",
            "description": "Tool-specific parameters",
        },
    },
}


def _make_tool_handler(
    tool_registry: ToolRegistry, tool_name: str
) -> Any:
    """Create an async handler that dispatches to a registered AURORA tool."""

    async def handler(params: dict[str, Any]) -> str:
        try:
            tool = tool_registry.get(tool_name)
        except KeyError:
            return json.dumps({"error": f"Tool '{tool_name}' not registered"})
        valid, msg = await tool.validate_params(params)
        if not valid:
            return json.dumps({"error": f"Invalid parameters: {msg}"})
        result = await tool.execute(params)
        return json.dumps({
            "success": result.success,
            "output": result.output,
            "error": result.error,
            "output_files": result.output_files,
            "metadata": result.metadata,
            "wall_time_seconds": result.wall_time_seconds,
        }, default=str)

    return handler


# ---------------------------------------------------------------------------
# Meta-tool builders
# ---------------------------------------------------------------------------

def _build_record_observation_tool(store: ArtifactStore) -> ToolSchema:
    """Tool that lets the agent record a scientific observation."""

    async def handler(params: dict[str, Any]) -> str:
        obs = Observation(
            observation_id=f"obs_{uuid.uuid4().hex[:8]}",
            source_job_id=params.get("source_job_id", "agent_direct"),
            description=params.get("description", ""),
            numerical_values=params.get("numerical_values", {}),
            timestamp=datetime.now(timezone.utc),
        )
        artifacts = store.load()
        store.record_observation(artifacts, obs)
        return json.dumps({
            "recorded": True,
            "observation_id": obs.observation_id,
        })

    return ToolSchema(
        name="record_observation",
        description=(
            "Record a scientific observation from experimental results. "
            "Always cite the source experiment's job_id."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Clear scientific description of the observation",
                },
                "source_job_id": {
                    "type": "string",
                    "description": "Job ID of the experiment that produced this observation",
                },
                "numerical_values": {
                    "type": "object",
                    "description": "Key numeric results, e.g. {\"segregation_energy\": -0.235, \"unit\": \"eV\"}",
                },
            },
            "required": ["description", "source_job_id"],
        },
        handler=handler,
    )


def _build_record_claim_tool(store: ArtifactStore) -> ToolSchema:
    """Tool that lets the agent propose a scientific claim."""

    async def handler(params: dict[str, Any]) -> str:
        from aurora.models.artifacts import ScientificClaim
        claim = ScientificClaim(
            claim_id=f"claim_{uuid.uuid4().hex[:8]}",
            statement=params["statement"],
            status="tentative",
            confidence=params.get("confidence", 0.5),
            supporting_evidence=params.get("supporting_evidence", []),
        )
        artifacts = store.load()
        store.add_claim(artifacts, claim)
        return json.dumps({
            "recorded": True,
            "claim_id": claim.claim_id,
            "status": "tentative",
        })

    return ToolSchema(
        name="record_claim",
        description=(
            "Propose a scientific claim based on accumulated evidence. "
            "Claims start as 'tentative' and must cite supporting observation/experiment IDs."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "statement": {
                    "type": "string",
                    "description": "Clear, falsifiable scientific claim statement",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence level 0.0-1.0",
                },
                "supporting_evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of observation_ids or job_ids supporting this claim",
                },
            },
            "required": ["statement", "supporting_evidence"],
        },
        handler=handler,
    )


def _build_read_artifacts_tool(store: ArtifactStore) -> ToolSchema:
    """Tool that lets the agent read current research artifacts."""

    async def handler(params: dict[str, Any]) -> str:
        artifacts = store.load()
        section = params.get("section", "summary")

        if section == "summary":
            return json.dumps({
                "objective": artifacts.initial_objective,
                "current_goal": artifacts.current_goal,
                "experiment_count": len(artifacts.experiments),
                "observation_count": len(artifacts.observations),
                "claim_count": len(artifacts.claims),
                "accepted_claims": sum(
                    1 for c in artifacts.claims if c.status == "accepted"
                ),
                "recent_observations": [
                    {"id": o.observation_id, "desc": o.description[:200]}
                    for o in artifacts.observations[-5:]
                ],
                "active_claims": [
                    {
                        "id": c.claim_id,
                        "statement": c.statement[:200],
                        "status": c.status,
                        "confidence": c.confidence,
                    }
                    for c in artifacts.claims
                    if c.status in ("tentative", "accepted")
                ],
            }, default=str)

        elif section == "experiments":
            return json.dumps([
                {
                    "job_id": e.job_id,
                    "skill": e.skill_used,
                    "status": e.status,
                    "failure_diagnosis": e.failure_diagnosis,
                }
                for e in artifacts.experiments[-10:]
            ], default=str)

        elif section == "observations":
            return json.dumps([
                {
                    "id": o.observation_id,
                    "source": o.source_job_id,
                    "description": o.description,
                    "values": o.numerical_values,
                }
                for o in artifacts.observations[-10:]
            ], default=str)

        elif section == "claims":
            return json.dumps([
                {
                    "id": c.claim_id,
                    "statement": c.statement,
                    "status": c.status,
                    "confidence": c.confidence,
                    "evidence": c.supporting_evidence,
                }
                for c in artifacts.claims
            ], default=str)

        return json.dumps({"error": f"Unknown section: {section}"})

    return ToolSchema(
        name="read_artifacts",
        description=(
            "Read current research artifacts — experiments, observations, claims. "
            "Use section='summary' for overview, 'experiments'/'observations'/'claims' for details."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["summary", "experiments", "observations", "claims"],
                    "description": "Which section to read",
                },
            },
        },
        handler=handler,
    )


def _build_decide_tool() -> ToolSchema:
    """Tool that lets the agent explicitly signal an iteration decision."""

    async def handler(params: dict[str, Any]) -> str:
        return json.dumps({
            "acknowledged": True,
            "decision": params.get("decision", "continue"),
            "reasoning": params.get("reasoning", ""),
        })

    return ToolSchema(
        name="decide_next_step",
        description=(
            "Signal what should happen next in the research iteration. "
            "Call this when you've completed your analysis for this iteration."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["continue", "replan", "audit", "done", "follow_up"],
                    "description": "Next action: continue (next experiment), replan (revise plan), audit (request review), done (goal achieved), follow_up (trigger new research cycle)",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief justification for this decision",
                },
            },
            "required": ["decision", "reasoning"],
        },
        handler=handler,
    )


def _build_request_audit_tool() -> ToolSchema:
    """Tool that lets the agent request a JIT audit."""

    async def handler(params: dict[str, Any]) -> str:
        return json.dumps({
            "audit_requested": True,
            "reason": params.get("reason", ""),
            "trigger": params.get("trigger", "agent_request"),
        })

    return ToolSchema(
        name="request_audit",
        description=(
            "Request a just-in-time audit when confidence is low, "
            "contradictions are found, or results seem anomalous."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why an audit is needed",
                },
                "trigger": {
                    "type": "string",
                    "enum": [
                        "low_confidence", "contradiction", "anomaly",
                        "repeated_failure", "convergence_issue",
                    ],
                },
            },
            "required": ["reason", "trigger"],
        },
        handler=handler,
    )


def _build_run_analysis_script_tool(workspace_dir: str) -> ToolSchema:
    """Tool that lets the agent write and execute a Python analysis script."""

    async def handler(params: dict[str, Any]) -> str:
        script = params.get("script", "")
        description = params.get("description", "unnamed analysis")

        if not script.strip():
            return json.dumps({"error": "Empty script"})

        # Security: basic checks
        dangerous = ["os.system", "subprocess.call", "shutil.rmtree", "os.remove", "__import__('os')"]
        for pattern in dangerous:
            if pattern in script:
                return json.dumps({"error": f"Blocked: script contains '{pattern}'"})

        script_id = f"analysis_{uuid.uuid4().hex[:8]}"
        script_dir = Path(workspace_dir) / "agent_scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / f"{script_id}.py"
        script_path.write_text(script, encoding="utf-8")

        try:
            proc = await asyncio.create_subprocess_exec(
                "python", str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_dir,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=60.0
            )
            return json.dumps({
                "success": proc.returncode == 0,
                "stdout": stdout.decode(errors="replace")[:4000],
                "stderr": stderr.decode(errors="replace")[:2000],
                "script_path": str(script_path),
            })
        except asyncio.TimeoutError:
            return json.dumps({"error": "Script timed out after 60s"})
        except Exception as exc:
            return json.dumps({"error": f"Script execution failed: {exc}"})

    return ToolSchema(
        name="run_analysis_script",
        description=(
            "Write and execute a Python script for custom data analysis. "
            "Use this when the available tools don't cover your specific analysis need. "
            "The script runs in the workspace directory and can import numpy, scipy, "
            "matplotlib, pandas, etc. Print results to stdout."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "Python script code to execute",
                },
                "description": {
                    "type": "string",
                    "description": "What this analysis does (for provenance)",
                },
            },
            "required": ["script", "description"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def build_agent_tools(
    tool_registry: ToolRegistry,
    artifact_store: ArtifactStore,
    workspace_dir: str = ".",
) -> list[ToolSchema]:
    """Build the complete set of Claude tool schemas for an agentic researcher.

    Returns computational tools from the registry plus meta-tools for
    observation/claim recording, artifact reading, and analysis scripting.
    """
    tools: list[ToolSchema] = []

    # 1. Computational tools from registry
    for tool_name in tool_registry.list_tools():
        schema = _TOOL_SCHEMAS.get(tool_name, _FALLBACK_SCHEMA)
        tool_obj = tool_registry.get(tool_name)
        tools.append(ToolSchema(
            name=tool_name,
            description=tool_obj.description or f"Computational tool: {tool_name}",
            input_schema=schema,
            handler=_make_tool_handler(tool_registry, tool_name),
        ))

    # 2. Meta-tools
    tools.append(_build_record_observation_tool(artifact_store))
    tools.append(_build_record_claim_tool(artifact_store))
    tools.append(_build_read_artifacts_tool(artifact_store))
    tools.append(_build_decide_tool())
    tools.append(_build_request_audit_tool())
    tools.append(_build_run_analysis_script_tool(workspace_dir))

    return tools
