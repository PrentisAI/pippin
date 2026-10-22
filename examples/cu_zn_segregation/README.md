# Case study: Zn segregation in Cu-Zn catalysts

The configuration of the materials-science campaign of the paper's end-to-end
case study. The objective and the quality gates the domain experts used are in
[`objective.md`](objective.md), which is passed to the agents as an input.

**This is a long, expensive campaign.** It runs plane-wave DFT; a full run
takes days on a 32-core machine and several hundred US$ of API use. Use the
[quickstart](../quickstart/) to try AURORA first.

```bash
docker build -t aurora-materials:latest examples/cu_zn_segregation   # QE, ASE, MACE, CHGNet
aurora doctor examples/cu_zn_segregation/config.yaml
aurora run examples/cu_zn_segregation/config.yaml
```

Notes

- The Dockerfile pins Quantum ESPRESSO 7.3 from conda-forge and downloads the
  SSSP pseudopotentials for Cu, Zn, C, O and H. Adjust it if your cluster has its
  own DFT build; any image works as long as the tools are on `PATH`.
- `human.approve_termination: true` makes the campaign ask a human before it
  finishes; in the paper, domain experts reviewed the final claims.
- With `sandbox.cpus: 32` the container is limited to 32 cores; the agents run
  `mpirun` inside it.
