# Sandbox of the milestone-auditor evaluation (image tag used by run_auditor.py:
# aurora-auditor-sandbox:py311-sci).  The auditor's Claude Code binary is mounted at run time
# (/opt/claude/claude), so the image only provides Python with the scientific stack the auditor
# uses to rerun the researchers' code.
#
#   docker build -t aurora-auditor-sandbox:py311-sci experiments/auditor
#
# Reconstructed from the image used for the paper (python:3.11.17-slim, Debian trixie, plus one
# `pip install numpy scipy pandas matplotlib scikit-learn` layer); sandbox-requirements.txt pins
# the versions that layer resolved to.
FROM python:3.11.17-slim

COPY sandbox-requirements.txt /tmp/sandbox-requirements.txt
RUN pip install --no-cache-dir -r /tmp/sandbox-requirements.txt && rm /tmp/sandbox-requirements.txt

CMD ["python3"]
