# Simulation environment for the Cu-Zn case study: Quantum ESPRESSO, ASE,
# pymatgen and two machine-learned interatomic potentials (MACE, CHGNet).
#   docker build -t aurora-materials:latest examples/cu_zn_segregation
FROM mambaorg/micromamba:1.5.10

RUN micromamba install -y -n base -c conda-forge \
        python=3.11 qe=7.3 openmpi ase pymatgen numpy scipy pandas matplotlib \
    && micromamba clean -a -y
ARG MAMBA_DOCKERFILE_ACTIVATE=1
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir mace-torch chgnet

# SSSP efficiency pseudopotentials for Cu, Zn, C, O, H
USER root
RUN mkdir -p /opt/pseudo && cd /opt/pseudo \
    && python - <<'PY'
import urllib.request
base = "https://pseudopotentials.quantum-espresso.org/upf_files/"
for name in ["Cu_pbe_v1.2.uspp.F.UPF", "Zn_pbe_v1.uspp.F.UPF", "C.pbe-n-kjpaw_psl.1.0.0.UPF",
             "O.pbe-n-kjpaw_psl.0.1.UPF", "H.pbe-rrkjus_psl.1.0.0.UPF"]:
    urllib.request.urlretrieve(base + name, name)
PY
ENV ESPRESSO_PSEUDO=/opt/pseudo OMP_NUM_THREADS=1
WORKDIR /app
