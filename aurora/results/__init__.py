"""A run's results and where each one came from.

The research agent records every number it will report (``aurora results record``): its value,
the script and command that produced it, the files it read and wrote, and the job.  The script is
copied when the number is recorded, so the record stays true when the script changes later.  The
report cites results as ``{R3}``; the console shows each as a chip that opens its provenance, the
export lists them in a table, ``reproduce.sh`` replays the commands, and runs are compared by them.
"""
