# Security

AURORA runs AI agents that write and execute code. The default setup confines
them to one Docker container per campaign and to the campaign directory on the
host (see docs/guide/sandbox.md). Settings that weaken this —
`sandbox.allow_host_bash`, exposing `aurora serve` on a public interface — are
documented as such.

To report a vulnerability (for example a way for an agent to escape the
sandbox, or to read files outside the campaign directory), please do not open a
public issue. Use GitHub's private vulnerability reporting ("Security" tab →
"Report a vulnerability"). We will acknowledge it within a week.
