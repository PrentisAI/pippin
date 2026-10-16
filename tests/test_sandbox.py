from aurora.llm.agent_loops import claude_code_env, permission_args


def test_without_container_only_workspace_files(tmp_path):
    args = permission_args(tmp_path)
    assert args[:2] == ["--permission-mode", "dontAsk"]
    allowed = args[3:]
    assert f"Read(/{tmp_path.resolve()}/**)" in allowed and f"Edit(/{tmp_path.resolve()}/**)" in allowed
    assert not any(a.startswith("Bash") for a in allowed)


def test_with_container_shell_only_through_docker_exec(tmp_path):
    allowed = permission_args(tmp_path, "abc123")[3:]
    bash = [a for a in allowed if a.startswith("Bash")]
    assert bash and all(a.startswith("Bash(docker exec") and "abc123" in a for a in bash)


def test_host_bash_only_when_allowed(tmp_path):
    assert "Bash" in permission_args(tmp_path, allow_host_bash=True)


def test_env_carries_only_what_claude_code_needs(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "other-key")
    env = claude_code_env("sk-or", "https://openrouter.ai/api")
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-or" and env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert "AWS_SECRET_ACCESS_KEY" not in env and "OPENAI_API_KEY" not in env
