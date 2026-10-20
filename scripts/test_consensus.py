#!/usr/bin/env python3
"""Offline acceptance tests for the consensus runner."""

import json
import os
from pathlib import Path
import runpy
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time


RUNNER = Path(__file__).with_name("consensus")


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time

mode = os.environ.get("FAKE_CLAUDE_MODE", "success")
recovery = os.environ.get("CLAUDE_CODE_DISABLE_THINKING") == "1"
prompt = sys.stdin.read()
is_synthesis = "FINAL AGREED ANSWER:" in prompt
pid_file = os.environ.get("FAKE_CLAUDE_PID_FILE")
if pid_file:
    with open(pid_file, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))

if mode in {"page_images", "large_page_image", "page_images_terminal_error"} and not recovery:
    sizes = [450000] * 6 if mode != "large_page_image" else [3100000]
    for size in sizes:
        print(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
            "tool_use_id": "offline-image", "content": [{"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": "A" * size}}]}]}}), flush=True)
    if mode == "page_images_terminal_error":
        print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True}), flush=True)
        raise SystemExit(0)

def answer(verdict, result):
    progress = "NONE: repeated search" if result == "none" else "NEW: " + result
    report = f"""CHECKPOINT
elapsed: 1 second
tentative_verdict: testing
new_results: {progress}
current_obstruction: none
next_bounded_step: done
token_cost: unavailable
END CHECKPOINT

Mock Claude report.
{result}
VERDICT: {verdict}"""
    print(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": report}]},
    }), flush=True)
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": report}), flush=True)

def plain(text):
    text = "CHECKPOINT\nelapsed: 1s\ntentative_verdict: AGREE\nnew_results: NEW: synthesis\ncurrent_obstruction: none\nnext_bounded_step: done\ntoken_cost: unavailable\nEND CHECKPOINT\n" + text + "\nVERDICT: AGREE"
    print(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }), flush=True)
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text}), flush=True)

call_log = os.environ.get("FAKE_CLAUDE_CALL_LOG")
if call_log:
    with open(call_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "argv": sys.argv[1:],
            "recovery": recovery,
            "synthesis": is_synthesis,
            "prompt_length": len(prompt),
            "thinking_tokens": os.environ.get("MAX_THINKING_TOKENS"),
            "thinking_disabled": os.environ.get("CLAUDE_CODE_DISABLE_THINKING") == "1",
            "effort_env": os.environ.get("CLAUDE_CODE_EFFORT_LEVEL"),
            "background_disabled": os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"),
            "subagent_rule": "- Sub-agents:" in prompt,
            "forbids_subagents": "delegate your role to a sub-agent" in prompt,
            "literature_open": "- Literature (opening round only)" in prompt,
            "literature_closed": "no longer available" in prompt,
        }) + "\n")

def paperclip_session(who, command, args, direct):
    """Act like a literature sub-agent (or a misbehaving parent) on the real helper."""
    import subprocess as sp
    revoke = args[args.index("--revoke") + 1]
    if direct:
        for _ in range(100):  # the runner revokes once it sees the direct call
            if os.path.exists(revoke):
                break
            time.sleep(0.05)
    call = lambda n, claim: {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {
        "name": "paperclip_evidence", "arguments": {"claim": claim}}}
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        call(2, "Statin therapy increases new-onset diabetes risk " + who),
        call(3, "A second claim that must be refused " + who)]
    proc = sp.Popen([command] + args, stdin=sp.PIPE, stdout=sp.PIPE, text=True)
    out, _ = proc.communicate("".join(json.dumps(m) + "\n" for m in messages), timeout=20)
    replies = [json.loads(line)["result"] for line in out.splitlines()][1:]
    results = os.environ.get("FAKE_LITERATURE_RESULTS")
    if results:
        with open(results, "a", encoding="utf-8") as handle:
            for n, result in enumerate(replies, 1):
                handle.write(json.dumps({"who": who, "call": n, "direct": direct,
                                         "revoked_seen": os.path.exists(revoke),
                                         "isError": result["isError"],
                                         "text": result["content"][0]["text"]}) + "\n")

if mode.startswith("literature") and not is_synthesis and not recovery:
    opening = "- Literature (opening round only)" in prompt
    if "--agents" in sys.argv:
        with open(sys.argv[sys.argv.index("--agents") + 1], encoding="utf-8") as handle:
            server = json.load(handle)["literature"]["mcpServers"][0]["paperclip"]
        direct = mode == "literature_direct"
        tool = {"type": "tool_use", "id": "lit-1", "name": "mcp__paperclip__paperclip_evidence",
                "input": {"claim": "statins raise diabetes risk"}}
        if direct:
            print(json.dumps({"type": "assistant", "message": {"content": [tool]}}), flush=True)
        else:
            print(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "agent-1", "name": "Agent",
                 "input": {"subagent_type": "literature"}}]}}), flush=True)
            print(json.dumps({"type": "assistant", "parent_tool_use_id": "agent-1",
                              "message": {"content": [tool]}}), flush=True)
        paperclip_session("Claude", server["command"], server["args"], direct)
    if opening:
        answer("DISAGREE", "literature evidence gathered")
    else:
        answer("AGREE", "evidence already cited")
    raise SystemExit(0)

if is_synthesis and mode == "synthesis_success":
    plain("Mock synthesis success.")
elif is_synthesis and mode == "synthesis_recover" and not recovery:
    time.sleep(20)
elif is_synthesis and mode == "synthesis_recover" and recovery:
    plain("Mock synthesis recovered.")
elif is_synthesis and mode == "synthesis_fail":
    raise SystemExit(3)
elif is_synthesis and mode == "synthesis_dissent":
    answer("DISAGREE", "I reject the agreed conclusion.")
elif mode == "oversize" and not recovery:
    answer("AGREE", "long supporting material " * 2500)
elif mode == "timeout_recover" and not recovery:
    time.sleep(20)
elif mode == "activity_deadline" and not recovery:
    for n in range(100):
        print(json.dumps({"type": "system", "subtype": "status", "n": n}), flush=True)
        time.sleep(0.1)
elif mode == "leader_exits" and not recovery:
    child = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)"
    ])
    child_pid_file = os.environ.get("FAKE_CHILD_PID_FILE")
    if child_pid_file:
        with open(child_pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    raise SystemExit(0)
elif mode == "interrupt" and not recovery:
    child = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)"
    ])
    child_pid_file = os.environ.get("FAKE_CHILD_PID_FILE")
    if child_pid_file:
        with open(child_pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    while True:
        print(json.dumps({"type": "system", "subtype": "status"}), flush=True)
        time.sleep(0.1)
elif mode == "die_on_term" and not recovery:
    # Like a real CLI hit by a supervisor's process-tree SIGTERM: exit at once.
    import signal
    signal.signal(signal.SIGTERM, lambda *_: os._exit(143))
    ready_file = os.environ.get("FAKE_CLAUDE_READY_FILE")
    if ready_file:
        open(ready_file, "w", encoding="utf-8").close()
    while True:
        print(json.dumps({"type": "system", "subtype": "status"}), flush=True)
        time.sleep(0.1)
elif mode == "activity":
    for n in range(7):
        print(json.dumps({"type": "system", "subtype": "status", "n": n}), flush=True)
        time.sleep(0.7)
    answer("AGREE", "activity reset exercised")
elif mode == "subagent_stream" and not recovery:
    leak = ("CHECKPOINT\nelapsed: 1s\ntentative_verdict: DISAGREE\n"
            "new_results: NEW: SUBAGENT-LEAK\ncurrent_obstruction: none\n"
            "next_bounded_step: done\ntoken_cost: unavailable\nEND CHECKPOINT\n"
            "SUBAGENT-LEAK\nVERDICT: DISAGREE")
    def subagent_message():
        print(json.dumps({"type": "assistant", "parent_tool_use_id": "offline-agent",
            "message": {"content": [{"type": "text", "text": leak}]}}), flush=True)
    subagent_message()
    report = ("CHECKPOINT\nelapsed: 1s\ntentative_verdict: AGREE\n"
              "new_results: NEW: parent verified sub-agent evidence\n"
              "current_obstruction: none\nnext_bounded_step: done\n"
              "token_cost: unavailable\nEND CHECKPOINT\nMock parent report.\nVERDICT: AGREE")
    print(json.dumps({"type": "assistant",
        "message": {"content": [{"type": "text", "text": report}]}}), flush=True)
    subagent_message()
    # An empty result string makes the runner fall back to the last parent message.
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "result": ""}), flush=True)
elif mode == "stalled":
    answer("DISAGREE", "none")
elif mode == "disagree":
    answer("DISAGREE", "substantive continuing result")
else:
    if mode == "page_images_terminal_error":
        print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True}), flush=True)
    else:
        answer("DISAGREE" if recovery and mode != "oversize" else "AGREE", "checkpoint exercised")
'''


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time

out = sys.argv[sys.argv.index("-o") + 1]
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_CODEX_MODE", "success")
print(json.dumps({"type": "thread.started", "thread_id": "00000000-0000-4000-8000-000000000001"}), flush=True)
if mode == "activity":
    for _ in range(12):
        print(json.dumps({"type": "turn.started"}), flush=True)
        time.sleep(0.1)
call_log = os.environ.get("FAKE_CODEX_CALL_LOG")
if call_log:
    with open(call_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "argv": sys.argv[1:],
            "prompt_length": len(prompt),
            "subagent_rule": "- Sub-agents:" in prompt,
            "literature_open": "- Literature (opening round only)" in prompt,
            "literature_closed": "no longer available" in prompt,
        }) + "\n")

def paperclip_session(who, command, args, direct):
    """Act like a literature sub-agent (or a misbehaving parent) on the real helper."""
    import subprocess as sp
    revoke = args[args.index("--revoke") + 1]
    if direct:
        for _ in range(100):  # the runner revokes once it sees the direct call
            if os.path.exists(revoke):
                break
            time.sleep(0.05)
    call = lambda n, claim: {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {
        "name": "paperclip_evidence", "arguments": {"claim": claim}}}
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        call(2, "Statin therapy increases new-onset diabetes risk " + who),
        call(3, "A second claim that must be refused " + who)]
    proc = sp.Popen([command] + args, stdin=sp.PIPE, stdout=sp.PIPE, text=True)
    out, _ = proc.communicate("".join(json.dumps(m) + "\n" for m in messages), timeout=20)
    replies = [json.loads(line)["result"] for line in out.splitlines()][1:]
    results = os.environ.get("FAKE_LITERATURE_RESULTS")
    if results:
        with open(results, "a", encoding="utf-8") as handle:
            for n, result in enumerate(replies, 1):
                handle.write(json.dumps({"who": who, "call": n, "direct": direct,
                                         "revoked_seen": os.path.exists(revoke),
                                         "isError": result["isError"],
                                         "text": result["content"][0]["text"]}) + "\n")

configs = dict(value.split("=", 1) for flag, value in zip(sys.argv, sys.argv[1:])
               if flag == "--config" and "=" in value)
if "mcp_servers.paperclip.command" in configs:
    direct = os.environ.get("FAKE_CODEX_LITERATURE") == "direct"
    if direct:
        print(json.dumps({"type": "item.started", "item": {"id": "item_9", "type": "mcp_tool_call",
            "server": "paperclip", "tool": "paperclip_evidence", "arguments": {"claim": "x"},
            "status": "in_progress"}}), flush=True)
    paperclip_session("Codex", json.loads(configs["mcp_servers.paperclip.command"]),
                      json.loads(configs["mcp_servers.paperclip.args"]), direct)
pid_file = os.environ.get("FAKE_CODEX_PID_FILE")
if pid_file:
    with open(pid_file, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
if mode == "leader_exits":
    child = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)"
    ])
    child_pid_file = os.environ.get("FAKE_CHILD_PID_FILE")
    if child_pid_file:
        with open(child_pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    raise SystemExit(0)
if mode == "interrupt":
    child = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)"
    ])
    child_pid_file = os.environ.get("FAKE_CHILD_PID_FILE")
    if child_pid_file:
        with open(child_pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    while True:
        time.sleep(0.1)
with open(out, "w", encoding="utf-8") as handle:
    verdict = "VERDICT: NOT AGREE" if mode == "malformed" else "VERDICT: AGREE"
    stalled = os.environ.get("FAKE_CLAUDE_MODE") == "stalled" and mode != "progress"
    if stalled:
        verdict = "VERDICT: DISAGREE"
    progress = "NONE: repeated search" if stalled else "NEW: independently checked answer"
    report = f"CHECKPOINT\nelapsed: 1s\ntentative_verdict: testing\nnew_results: {progress}\ncurrent_obstruction: none\nnext_bounded_step: done\ntoken_cost: unavailable\nEND CHECKPOINT\nMock Codex report.\n{verdict}\n"
    if mode == "missing_checkpoint":
        report = "I endorse the answer.\nVERDICT: AGREE\n"
    if mode == "literature" and "- Literature (opening round only)" in prompt:
        report = report.replace("VERDICT: AGREE", "VERDICT: DISAGREE")
    if mode == "oversize_once" and "resume" not in sys.argv:
        report = report.replace("Mock Codex report.", "x" * 50000)
    handle.write(report)
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": report}}), flush=True)
print(json.dumps({"type": "turn.completed"}), flush=True)
'''


def write_executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def assert_process_gone(pid):
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f"subprocess {pid} survived runner cleanup")


def run_case(root, fake_bin, name, mode, extra_args=None, *, synthesize=False,
             max_rounds=1, expected_code=0, extra_env=None):
    save = root / f"{name}.md"
    progress = root / f"{name}.progress.md"
    env = dict(os.environ)
    for key in (
        "CONSENSUS_CLAUDE_MODEL",
        "CONSENSUS_CLAUDE_EFFORT",
        "CONSENSUS_CODEX_MODEL",
        "CONSENSUS_CODEX_REASONING_EFFORT",
    ):
        env.pop(key, None)
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["FAKE_CLAUDE_MODE"] = mode
    if extra_env:
        env.update(extra_env)
    command = [
        str(RUNNER), "--quiet", "--max-rounds", str(max_rounds),
        "--claude-report-deadline", "1",
        "--claude-recovery-timeout", "2", "--save", str(save),
        "--progress", str(progress),
    ]
    if not synthesize:
        command.append("--no-synthesize")
    if extra_args:
        command.extend(extra_args)
    command.append(name)
    proc = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != expected_code:
        raise AssertionError(proc.stderr or proc.stdout)
    journal = Path(str(progress) + ".claude-stream.jsonl")
    rows = [json.loads(line) for line in journal.read_text().splitlines() if line]
    assert rows and rows[0]["type"] == "consensus_stream_journal"
    assert save.exists() and progress.exists()
    manifest = Path(str(progress) + ".protocol.json")
    protocol = json.loads(manifest.read_text(encoding="utf-8"))
    assert protocol["stream_activity_extends_absolute_deadline"] is False
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    return (
        progress.read_text(encoding="utf-8"),
        rows,
        transcript.read_text(encoding="utf-8") if (transcript := save).exists() else "",
    )


def fake_paperclip():
    """A local stand-in for the Paperclip API that records every request."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
            paper = {"id": "PMC111", "title": "Statins and incident diabetes", "doi": "10.1/111",
                     "pmid": "111", "year": 2010, "design": "meta_analysis", "citations": 900,
                     "retracted": False}
            data = json.dumps({"id": "req_1", "object": "claim_evidence", "claim": body["claim"],
                               "model_version": "gxl-evidence-offline",
                               "counts": {"retrieved": 10, "supports": 1, "refutes": 0},
                               "supports": [{"evidence": "Statins modestly raised risk.",
                                             "confidence": 0.97, "paper": paper}],
                               "refutes": [], "timing_ms": {"total": 1}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def literature_cases(root, fake_bin):
    """--literature: one Evidence API call per participant, by a sub-agent, in round 1."""
    # Defaults stay byte-for-byte free of Paperclip.
    default_claude = json.loads((root / "latest-claude.jsonl").read_text().splitlines()[0])
    default_codex = json.loads((root / "latest-codex.jsonl").read_text().splitlines()[0])
    assert "--agents" not in default_claude["argv"] and not default_claude["literature_open"]
    assert not any("mcp_servers." in arg for arg in default_codex["argv"])
    assert json.loads((root / "latest-high-defaults.progress.md.protocol.json")
                      .read_text())["literature"] == {"enabled": False}

    # Preflight refusals happen before any artifact or provider call.
    no_key = {k: v for k, v in os.environ.items() if k != "PAPERCLIP_API_KEY"}
    for args, env, reason in ((["--literature"], {**no_key, "PAPERCLIP_API_KEY": "k"}, "--subagents N"),
                              (["--literature", "--subagents", "1"], no_key, "PAPERCLIP_API_KEY")):
        refused = subprocess.run([str(RUNNER), *args, "--save", str(root / "refused.md"), "q"],
                                 capture_output=True, text=True, env=env)
        assert refused.returncode == 2 and reason in refused.stderr, refused.stderr
        assert not (root / "refused.md").exists()

    server, seen = fake_paperclip()
    try:
        env = {"PAPERCLIP_API_KEY": "gxl_offline_key",
               "CONSENSUS_PAPERCLIP_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
               "FAKE_CODEX_MODE": "literature"}
        claude_log, codex_log = root / "lit-claude.jsonl", root / "lit-codex.jsonl"
        results = root / "lit-results.jsonl"
        progress, _, transcript = run_case(
            root, fake_bin, "literature", "literature", max_rounds=2,
            extra_args=["--subagents", "2", "--literature"],
            extra_env={**env, "FAKE_CLAUDE_CALL_LOG": str(claude_log),
                       "FAKE_CODEX_CALL_LOG": str(codex_log),
                       "FAKE_LITERATURE_RESULTS": str(results)})
        assert "CONSENSUS REACHED" in transcript, transcript
        claude_calls = [json.loads(line) for line in claude_log.read_text().splitlines()]
        codex_calls = [json.loads(line) for line in codex_log.read_text().splitlines()]
        assert len(claude_calls) == 2 and len(codex_calls) == 2, (claude_calls, codex_calls)
        # Opening round: the rule and the Paperclip wiring, for both participants.
        opening_claude, later_claude = claude_calls
        assert opening_claude["literature_open"] and not opening_claude["literature_closed"]
        argv = opening_claude["argv"]
        assert argv[argv.index("--allowedTools") + 1] == "mcp__paperclip__paperclip_evidence"
        assert codex_calls[0]["literature_open"]
        assert 'mcp_servers.paperclip.default_tools_approval_mode="approve"' in codex_calls[0]["argv"]
        assert 'mcp_servers.paperclip.env_vars=["PAPERCLIP_API_KEY"]' in codex_calls[0]["argv"]
        # Later rounds: no wiring, and the prompt says the window has closed.
        assert later_claude["literature_closed"] and not later_claude["literature_open"]
        assert "--agents" not in later_claude["argv"] and "--allowedTools" not in later_claude["argv"]
        assert codex_calls[1]["literature_closed"] and "resume" in codex_calls[1]["argv"]
        assert not any("mcp_servers." in arg for arg in codex_calls[1]["argv"])
        # Each participant's sub-agent reached the Evidence API exactly once; the second is refused.
        assert [r["path"] for r in seen] == ["/claims/support"] * 2, seen
        assert sorted(r["body"]["claim"] for r in seen) == [
            "Statin therapy increases new-onset diabetes risk Claude",
            "Statin therapy increases new-onset diabetes risk Codex"], seen
        assert all(r["auth"] == "Bearer gxl_offline_key" and r["body"]["include_context"] is False
                   for r in seen)
        outcomes = [json.loads(line) for line in results.read_text().splitlines()]
        assert sorted((o["who"], o["call"], o["isError"]) for o in outcomes) == [
            ("Claude", 1, False), ("Claude", 2, True), ("Codex", 1, False), ("Codex", 2, True)]
        for o in outcomes:
            assert ("10.1/111" in o["text"]) if o["call"] == 1 else ("already been used" in o["text"])
        log = Path(str(root / "literature.progress.md") + ".literature.jsonl")
        assert stat.S_IMODE(log.stat().st_mode) == 0o600
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert sorted((r["participant"], r["status"]) for r in rows) == [
            ("Claude", "ok"), ("Claude", "refused"), ("Codex", "ok"), ("Codex", "refused")]
        assert "gxl_offline_key" not in log.read_text()
        assert progress.count("literature round finished") == 2
        assert ("requests: 2 (ok 1, errors 0, refused as over budget 1, refused after revocation 0, "
                "invalid 0)") in progress
        assert "literature access revoked" not in progress
        manifest = json.loads(Path(str(root / "literature.progress.md") + ".protocol.json").read_text())
        assert manifest["literature"]["enabled"] and manifest["literature"]["round"] == 1
        assert manifest["literature"]["access"] == "research sub-agents only"
        assert manifest["literature"]["direct_call_revocations"] == {"Claude": 0, "Codex": 0}
        assert manifest["literature"]["log"] == str(log)
        assert manifest["literature"]["tools"] == ["paperclip_evidence"]
        assert manifest["literature"]["calls_per_participant"] == 1

        # A participant calling Paperclip itself is logged, revoked, and the debate goes on.
        seen.clear()
        results = root / "lit-direct-results.jsonl"
        progress, _, transcript = run_case(
            root, fake_bin, "literature-direct", "literature_direct", max_rounds=2,
            extra_args=["--subagents", "1", "--literature"],
            extra_env={**env, "FAKE_CODEX_LITERATURE": "direct",
                       "FAKE_LITERATURE_RESULTS": str(results)})
        assert "CONSENSUS REACHED" in transcript, transcript
        outcomes = [json.loads(line) for line in results.read_text().splitlines()]
        assert len(outcomes) == 4
        for o in outcomes:
            assert o["revoked_seen"] and o["isError"] and "revoked" in o["text"], o
        for who in ("Claude", "Codex"):
            assert f"{who} literature access revoked" in progress
        assert not seen, "a revoked call reached Paperclip"
        assert "refused after revocation 2" in progress
        manifest = json.loads(Path(str(root / "literature-direct.progress.md")
                                   + ".protocol.json").read_text())
        assert manifest["literature"]["direct_call_revocations"] == {"Claude": 1, "Codex": 1}

        # Recovery in the opening round never receives Paperclip.
        claude_log, codex_log = root / "lit-recovery-claude.jsonl", root / "lit-recovery-codex.jsonl"
        recovery_progress, _, _ = run_case(
            root, fake_bin, "literature-recovery", "timeout_recover",
            extra_args=["--subagents", "2", "--literature"],
            extra_env={**env, "FAKE_CLAUDE_CALL_LOG": str(claude_log),
                       "FAKE_CODEX_CALL_LOG": str(codex_log), "FAKE_CODEX_MODE": "oversize_once"})
        assert recovery_progress.count("recovery: yes") >= 2
        claude_calls = [json.loads(line) for line in claude_log.read_text().splitlines()]
        codex_calls = [json.loads(line) for line in codex_log.read_text().splitlines()]
        assert "--agents" in claude_calls[0]["argv"] and claude_calls[0]["literature_open"]
        assert claude_calls[1]["recovery"] and "--agents" not in claude_calls[1]["argv"]
        assert claude_calls[1]["literature_closed"] and not claude_calls[1]["literature_open"]
        assert any("mcp_servers.paperclip" in arg for arg in codex_calls[0]["argv"])
        assert "resume" in codex_calls[1]["argv"]
        assert not any("mcp_servers." in arg for arg in codex_calls[1]["argv"])
    finally:
        server.shutdown()


def main():
    with tempfile.TemporaryDirectory(prefix="consensus-skill-test-") as tmp:
        root = Path(tmp)
        docs = root / "model-docs"
        docs.mkdir()
        (docs / "openai.md").write_text(
            "---\nlatestModelInfo:\n  model: gpt-99-flagship\n---\n"
            "Old examples use gpt-6-astra.\n", encoding="utf-8")
        (docs / "claude.md").write_text(
            "## Compare models\n"
            "| Feature | New flagship | Cheaper model |\n"
            "| Description | For demanding reasoning and agentic work | Fast |\n"
            "| Claude API ID | " + chr(96) + "claude-next-99" + chr(96)
            + " | claude-haiku-100 |\n"
            "## Legacy\nclaude-fable-5-1\n", encoding="utf-8")
        # Every subprocess in this suite uses explicit offline doc fixtures.
        os.environ["CONSENSUS_MODEL_DOCS_DIR"] = str(docs)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        write_executable(fake_bin / "claude", textwrap.dedent(FAKE_CLAUDE))
        write_executable(fake_bin / "codex", textwrap.dedent(FAKE_CODEX))

        success, _, _ = run_case(root, fake_bin, "success", "success")
        assert "Run completed" in success

        for image_mode in ("page_images", "large_page_image"):
            image_progress, image_rows, image_transcript = run_case(root, fake_bin, image_mode, image_mode)
            assert "CONSENSUS REACHED" in image_transcript and "invalid report" not in image_progress
            assert sum(len(row.get("line", "").encode("utf-8")) for row in image_rows) > 2_000_000
            assert "image/png" in "".join(row.get("line", "") for row in image_rows)
        error_progress, _, error_transcript = run_case(
            root, fake_bin, "images-terminal-error", "page_images_terminal_error", expected_code=1,
        )
        assert "terminal failure" in error_progress and "CONSENSUS REACHED" not in error_transcript

        claude_log = root / "latest-claude.jsonl"
        codex_log = root / "latest-codex.jsonl"
        run_case(
            root,
            fake_bin,
            "latest-high-defaults",
            "success",
            extra_env={
                "FAKE_CLAUDE_CALL_LOG": str(claude_log),
                "FAKE_CODEX_CALL_LOG": str(codex_log),
            },
        )
        neutral_claude_args = json.loads(claude_log.read_text().splitlines()[0])["argv"]
        neutral_codex_args = json.loads(codex_log.read_text().splitlines()[0])["argv"]
        assert neutral_claude_args[neutral_claude_args.index("--model") + 1] == "claude-opus-5-5"
        assert neutral_claude_args[neutral_claude_args.index("--effort") + 1] == "xhigh"
        assert neutral_codex_args[neutral_codex_args.index("--model") + 1] == "gpt-6.1-sol"
        assert 'model_reasoning_effort="xhigh"' in neutral_codex_args
        assert json.loads(claude_log.read_text().splitlines()[0])["effort_env"] == "xhigh"
        protocol = json.loads((root / "latest-high-defaults.progress.md.protocol.json").read_text())
        assert protocol["model_selection"]["models"]["openai"]["model"] == "gpt-6.1-sol"
        assert protocol["model_selection"]["models"]["claude"] == {
            "model": "claude-opus-5-5", "effort": "xhigh",
            "source": "skill default: claude-opus-5-5",
        }

        resolver_env = dict(os.environ)
        for key in ("CONSENSUS_CLAUDE_MODEL", "CONSENSUS_CODEX_MODEL",
                    "CONSENSUS_CLAUDE_EFFORT", "CONSENSUS_CODEX_REASONING_EFFORT"):
            resolver_env.pop(key, None)
        resolved = subprocess.run([str(RUNNER), "--resolve-models"],
                                  env=resolver_env, capture_output=True, text=True, check=True)
        assert json.loads(resolved.stdout)["models"]["claude"]["effort"] == "xhigh"
        # A changing or missing Claude flagship table must not change the default.
        openai_only = root / "openai-only-docs"
        openai_only.mkdir()
        (openai_only / "openai.md").write_text((docs / "openai.md").read_text())
        resolver_env["CONSENSUS_MODEL_DOCS_DIR"] = str(openai_only)
        default_claude = subprocess.run([str(RUNNER), "--resolve-models"],
                                       env=resolver_env, capture_output=True,
                                       text=True, check=True)
        assert json.loads(default_claude.stdout)["models"]["claude"] == {
            "model": "claude-opus-5-5", "effort": "xhigh",
            "source": "skill default: claude-opus-5-5",
        }
        # Malformed documentation must fail before either participant starts.
        malformed = root / "malformed-docs"
        malformed.mkdir()
        (malformed / "openai.md").write_text("Historical mention: gpt-5.5\n")
        resolver_env["CONSENSUS_MODEL_DOCS_DIR"] = str(malformed)
        failed = subprocess.run([str(RUNNER), "--resolve-models"],
                                env=resolver_env, capture_output=True, text=True)
        assert failed.returncode == 0  # Fixed defaults never consult an incidental document snapshot.
        # Explicit pins work without readable current documentation.
        resolver_env.update(CONSENSUS_CODEX_MODEL="pinned-openai",
                            CONSENSUS_CLAUDE_MODEL="pinned-claude")
        pinned = subprocess.run([str(RUNNER), "--resolve-models"],
                                env=resolver_env, capture_output=True, text=True, check=True)
        assert json.loads(pinned.stdout)["models"]["claude"]["model"] == "pinned-claude"
        symbols = runpy.run_path(str(RUNNER))
        for bad in (
            "## Compare models\n| Description | Fast |\n| Claude API ID | claude-cheap |\n",
            "## Compare models\n| Description | Most capable | Demanding reasoning |\n"
            "| Claude API ID | claude-one | claude-two |\n",
        ):
            try:
                symbols["parse_claude_model"](bad)
            except ValueError:
                pass
            else:
                raise AssertionError("ambiguous/nonflagship Claude table was accepted")

        claude_log = root / "override-claude.jsonl"
        codex_log = root / "override-codex.jsonl"
        run_case(
            root,
            fake_bin,
            "model-overrides",
            "success",
            extra_env={
                "FAKE_CLAUDE_CALL_LOG": str(claude_log),
                "FAKE_CODEX_CALL_LOG": str(codex_log),
                "CONSENSUS_CLAUDE_MODEL": "mock-claude",
                "CONSENSUS_CLAUDE_EFFORT": "high",
                "CONSENSUS_CODEX_MODEL": "mock-codex",
                "CONSENSUS_CODEX_REASONING_EFFORT": "high",
            },
        )
        override_claude_args = json.loads(claude_log.read_text().splitlines()[0])["argv"]
        override_codex_args = json.loads(codex_log.read_text().splitlines()[0])["argv"]
        assert override_claude_args[override_claude_args.index("--model") + 1] == "mock-claude"
        assert override_claude_args[override_claude_args.index("--effort") + 1] == "high"
        assert override_codex_args[override_codex_args.index("--model") + 1] == "mock-codex"
        assert 'model_reasoning_effort="high"' in override_codex_args

        # Sub-agents stay forbidden unless a run opts in with --subagents N.
        default_claude = json.loads((root / "latest-claude.jsonl").read_text().splitlines()[0])
        default_codex = json.loads((root / "latest-codex.jsonl").read_text().splitlines()[0])
        assert default_claude["background_disabled"] is None
        assert default_claude["forbids_subagents"] and not default_claude["subagent_rule"]
        assert not default_codex["subagent_rule"]
        assert not any(arg.startswith(("agents.", "features.multi_agent"))
                       for arg in default_codex["argv"])
        assert json.loads((root / "latest-high-defaults.progress.md.protocol.json")
                          .read_text())["research_subagents_per_participant"] == 0

        claude_log = root / "subagents-claude.jsonl"
        codex_log = root / "subagents-codex.jsonl"
        sub_progress, _, sub_transcript = run_case(
            root, fake_bin, "subagents", "subagent_stream", extra_args=["--subagents", "3"],
            extra_env={"FAKE_CLAUDE_CALL_LOG": str(claude_log),
                       "FAKE_CODEX_CALL_LOG": str(codex_log)},
        )
        sub_claude = json.loads(claude_log.read_text().splitlines()[0])
        sub_codex = json.loads(codex_log.read_text().splitlines()[0])
        assert sub_claude["background_disabled"] == "1"
        assert sub_claude["subagent_rule"] and not sub_claude["forbids_subagents"]
        assert sub_codex["subagent_rule"]
        for flag in ("features.multi_agent=true",
                     "agents.max_concurrent_threads_per_session=3", "agents.max_depth=1"):
            assert flag in sub_codex["argv"]
        # Sub-agent text never becomes the participant's checkpoint or report.
        assert "CONSENSUS REACHED" in sub_transcript and "Mock parent report." in sub_transcript
        assert "SUBAGENT-LEAK" not in sub_progress + sub_transcript
        assert "up to 3 at a time" in sub_progress
        assert json.loads((root / "subagents.progress.md.protocol.json")
                          .read_text())["research_subagents_per_participant"] == 3

        # Recovery tranches never receive sub-agents, for either participant.
        claude_log = root / "subagents-recovery-claude.jsonl"
        codex_log = root / "subagents-recovery-codex.jsonl"
        sub_recovery, _, _ = run_case(
            root, fake_bin, "subagents-recovery", "timeout_recover",
            extra_args=["--subagents", "2"],
            extra_env={"FAKE_CLAUDE_CALL_LOG": str(claude_log),
                       "FAKE_CODEX_CALL_LOG": str(codex_log),
                       "FAKE_CODEX_MODE": "oversize_once"},
        )
        assert sub_recovery.count("recovery: yes") >= 2
        claude_calls = [json.loads(line) for line in claude_log.read_text().splitlines()]
        codex_calls = [json.loads(line)["argv"] for line in codex_log.read_text().splitlines()]
        assert claude_calls[0]["background_disabled"] == "1"
        assert claude_calls[1]["background_disabled"] is None
        assert "agents.max_concurrent_threads_per_session=2" in codex_calls[0]
        assert "resume" in codex_calls[1]
        assert not any(arg.startswith("agents.") for arg in codex_calls[1])

        rejected = subprocess.run([str(RUNNER), "--subagents", "-1", "--no-save", "q"],
                                  capture_output=True, text=True)
        assert rejected.returncode == 2 and "nonnegative" in rejected.stderr

        literature_cases(root, fake_bin)
        mixed = "\n".join(json.dumps(event) for event in (
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "parent"}]}},
            {"type": "assistant", "parent_tool_use_id": "offline-agent",
             "message": {"content": [{"type": "text", "text": "sub-agent"}]}},
            {"type": "result", "subtype": "success", "is_error": False, "result": ""},
        ))
        assert symbols["claude_answer"](mixed)[1] == "parent"

        invalid_progress, _, invalid_transcript = run_case(
            root,
            fake_bin,
            "invalid-codex-verdict",
            "success",
            expected_code=1,
            extra_env={"FAKE_CODEX_MODE": "malformed"},
        )
        assert "Codex invalid report 1/2" in invalid_progress
        assert "Codex invalid report 2/2" in invalid_progress
        assert "ABORTED" in invalid_transcript

        parity, _, parity_transcript = run_case(
            root, fake_bin, "codex-checkpoint-required", "success", expected_code=1,
            extra_env={"FAKE_CODEX_MODE": "missing_checkpoint"},
        )
        assert "Codex invalid report 2/2" in parity
        assert "CONSENSUS REACHED" not in parity_transcript

        _, _, reused = run_case(root, fake_bin, "no-clobber", "success")
        _, _, untouched = run_case(root, fake_bin, "no-clobber", "success", expected_code=2)
        assert reused == untouched
        run_case(root, fake_bin, "no-clobber", "success", extra_args=["--overwrite"])

        recovery_log = root / "research-recovery-calls.jsonl"
        recovery, _, _ = run_case(
            root,
            fake_bin,
            "recovery",
            "timeout_recover",
            extra_env={"FAKE_CLAUDE_CALL_LOG": str(recovery_log)},
        )
        assert "silent 5/5" in recovery
        assert "five consecutive silent intervals" in recovery
        assert "recovery: yes" in recovery
        recovery_calls = [
            json.loads(line) for line in recovery_log.read_text().splitlines()
        ]
        initial_call, recovered_call = recovery_calls[0], recovery_calls[1]
        initial_args, recovered_args = initial_call["argv"], recovered_call["argv"]
        research_session = initial_args[initial_args.index("--session-id") + 1]
        assert recovered_args[recovered_args.index("--resume") + 1] == research_session
        assert initial_call["thinking_tokens"] == "7000"
        assert initial_call["thinking_disabled"] is False
        assert recovered_call["thinking_tokens"] is None
        assert recovered_call["thinking_disabled"] is True

        activity, rows, _ = run_case(root, fake_bin, "activity", "activity")
        assert "stream activity observed" in activity
        assert "terminated after five silent intervals" not in activity
        assert len(rows) > 2

        codex_activity, _, _ = run_case(
            root, fake_bin, "codex-activity-log", "success",
            extra_env={"FAKE_CODEX_MODE": "activity"},
        )
        block = codex_activity.split("Codex stream activity observed", 1)[1].split("## ", 1)[0]
        assert ".codex-stream.jsonl" in block and ".claude-stream.jsonl" not in block

        hard_cap, _, _ = run_case(
            root,
            fake_bin,
            "activity-deadline",
            "activity_deadline",
            extra_args=["--claude-turn-timeout", "2"],
        )
        assert "hard deadline exceeded after 2s" in hard_cap
        assert "recovery: yes" in hard_cap

        runner_symbols = runpy.run_path(str(RUNNER))
        verdict_of = runner_symbols["verdict_of"]
        assert verdict_of("x\nVERDICT: AGREE\n") == "AGREE"
        assert verdict_of("x\nVERDICT: DISAGREE\n") == "DISAGREE"
        assert verdict_of("VERDICT: NOT AGREE") is None
        assert verdict_of("VERDICT: DISAGREEMENT") is None
        assert verdict_of("VERDICT: AGREE\ntrailing material") is None

        call_log = root / "synthesis-calls.jsonl"
        synth_progress, _, synth_transcript = run_case(
            root,
            fake_bin,
            "synthesis-success",
            "synthesis_success",
            synthesize=True,
            extra_env={"FAKE_CLAUDE_CALL_LOG": str(call_log)},
        )
        assert "Claude synthesis completed" in synth_progress
        assert "Mock synthesis success." in synth_transcript

        call_log.write_text("", encoding="utf-8")
        recovered_progress, _, recovered_transcript = run_case(
            root,
            fake_bin,
            "synthesis-recovered",
            "synthesis_recover",
            extra_args=["--claude-turn-timeout", "2"],
            synthesize=True,
            extra_env={"FAKE_CLAUDE_CALL_LOG": str(call_log)},
        )
        assert "Claude synthesis recovered" in recovered_progress
        assert "Mock synthesis recovered." in recovered_transcript
        calls = [json.loads(line) for line in call_log.read_text().splitlines()]
        synth_calls = [call for call in calls if call["synthesis"]]
        assert len(synth_calls) == 2
        first_args, recovered_args = synth_calls[0]["argv"], synth_calls[1]["argv"]
        session_id = first_args[first_args.index("--session-id") + 1]
        assert recovered_args[recovered_args.index("--resume") + 1] == session_id

        failed_progress, _, failed_transcript = run_case(
            root,
            fake_bin,
            "synthesis-fallback",
            "synthesis_fail",
            synthesize=True,
        )
        assert "synthesis recovery failed" in failed_progress
        assert "Mock Codex report." in failed_transcript

        dissent_progress, _, dissent_transcript = run_case(
            root, fake_bin, "synthesis-dissent", "synthesis_dissent", synthesize=True,
        )
        assert "synthesis recovery failed" in dissent_progress
        agreed_body = dissent_transcript.split("## Agreed answer", 1)[1].split("## Claude", 1)[0]
        assert "Mock Codex report." in agreed_body and "I reject" not in agreed_body

        compression, _, compressed = run_case(root, fake_bin, "compressed-report", "oversize")
        assert "byte delivery limit" in compression
        assert "recovery: yes" in compression and "CONSENSUS REACHED" in compressed

        codex_compression, _, codex_compressed = run_case(
            root, fake_bin, "codex-compressed-report", "success",
            extra_env={"FAKE_CODEX_MODE": "oversize_once"},
        )
        assert "byte delivery limit" in codex_compression
        assert "recovery: yes" in codex_compression and "CONSENSUS REACHED" in codex_compressed

        stalled_progress, _, stalled_transcript = run_case(
            root,
            fake_bin,
            "stalled",
            "stalled",
            max_rounds=2,
        )
        assert "Debate stalled" in stalled_progress
        assert "NO CONSENSUS — STALLED" in stalled_transcript
        assert stalled_transcript.count("## Claude") == 1
        assert stalled_transcript.count("## Codex") == 1

        _, _, progressing = run_case(
            root, fake_bin, "peer-progress-resets-stall", "stalled", max_rounds=2,
            extra_env={"FAKE_CODEX_MODE": "progress"},
        )
        assert "STALLED" not in progressing
        assert progressing.count("## Claude") == 2 and progressing.count("## Codex") == 2

        budget_log = root / "aggregate-thinking-calls.jsonl"
        codex_budget_log = root / "codex-continuation-calls.jsonl"
        run_case(
            root,
            fake_bin,
            "aggregate-thinking-budget",
            "disagree",
            max_rounds=6,
            extra_env={"FAKE_CLAUDE_CALL_LOG": str(budget_log),
                       "FAKE_CODEX_CALL_LOG": str(codex_budget_log)},
        )
        budget_calls = [
            json.loads(line) for line in budget_log.read_text().splitlines()
        ]
        fresh_allocations = [
            int(call["thinking_tokens"])
            for call in budget_calls
            if call["thinking_tokens"] is not None
        ]
        assert sum(fresh_allocations) == 42000
        assert all(allocation <= 7000 for allocation in fresh_allocations)
        initial = budget_calls[0]["argv"]
        stable_id = initial[initial.index("--session-id") + 1]
        for call in budget_calls[1:]:
            assert call["argv"][call["argv"].index("--resume") + 1] == stable_id
        codex_calls = [json.loads(line)["argv"] for line in codex_budget_log.read_text().splitlines()]
        assert "resume" not in codex_calls[0]
        assert all("resume" in argv and "00000000-0000-4000-8000-000000000001" in argv
                   for argv in codex_calls[1:])

        large_context = root / "large-context.txt"
        large_context.write_text("x" * 1_200_000, encoding="utf-8")
        _, _, _ = run_case(
            root,
            fake_bin,
            "large-stdin-prompt",
            "success",
            extra_args=["--context", str(large_context)],
        )

        # Terminating the runner must reap the active model process group and
        # leave an INTERRUPTED transcript, even while the model is streaming.
        save = root / "interrupted.md"
        progress = root / "interrupted.progress.md"
        pid_file = root / "interrupted-model.pid"
        child_pid_file = root / "interrupted-child.pid"
        env = dict(os.environ)
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_CLAUDE_MODE"] = "interrupt"
        env["FAKE_CLAUDE_PID_FILE"] = str(pid_file)
        env["FAKE_CHILD_PID_FILE"] = str(child_pid_file)
        command = [
            str(RUNNER), "--quiet", "--max-rounds", "1", "--no-synthesize",
            "--claude-report-deadline", "1", "--claude-turn-timeout", "30",
            "--save", str(save), "--progress", str(progress),
            "interruption cleanup",
        ]
        running = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(100):
            if ((pid_file.exists() and child_pid_file.exists())
                    or running.poll() is not None):
                break
            time.sleep(0.05)
        assert pid_file.exists(), "fake Claude process never started"
        assert child_pid_file.exists(), "fake Claude child never started"
        model_pid = int(pid_file.read_text(encoding="utf-8"))
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        running.terminate()
        stdout, stderr = running.communicate(timeout=10)
        assert running.returncode == 130, (stdout, stderr)
        assert "INTERRUPTED" in save.read_text(encoding="utf-8")
        assert_process_gone(model_pid)
        assert_process_gone(child_pid)

        # Regression for the original race: the group leader exits normally
        # before cleanup while its descendant remains alive.
        child_pid_file = root / "claude-leader-exits-child.pid"
        run_case(
            root,
            fake_bin,
            "claude-leader-exits",
            "leader_exits",
            extra_env={"FAKE_CHILD_PID_FILE": str(child_pid_file)},
        )
        assert child_pid_file.exists()
        assert_process_gone(int(child_pid_file.read_text(encoding="utf-8")))

        child_pid_file = root / "codex-leader-exits-child.pid"
        run_case(
            root,
            fake_bin,
            "codex-leader-exits",
            "success",
            expected_code=1,
            extra_env={
                "FAKE_CODEX_MODE": "leader_exits",
                "FAKE_CHILD_PID_FILE": str(child_pid_file),
            },
        )
        assert child_pid_file.exists()
        assert_process_gone(int(child_pid_file.read_text(encoding="utf-8")))

        # The same process-group guarantee applies when Codex is active.
        save = root / "interrupted-codex.md"
        progress = root / "interrupted-codex.progress.md"
        pid_file = root / "interrupted-codex-model.pid"
        child_pid_file = root / "interrupted-codex-child.pid"
        env = dict(os.environ)
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_CLAUDE_MODE"] = "success"
        env["FAKE_CODEX_MODE"] = "interrupt"
        env["FAKE_CODEX_PID_FILE"] = str(pid_file)
        env["FAKE_CHILD_PID_FILE"] = str(child_pid_file)
        command = [
            str(RUNNER), "--first", "codex", "--quiet", "--max-rounds", "1",
            "--no-synthesize", "--save", str(save), "--progress", str(progress),
            "Codex interruption cleanup",
        ]
        running = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(100):
            if ((pid_file.exists() and child_pid_file.exists())
                    or running.poll() is not None):
                break
            time.sleep(0.05)
        assert pid_file.exists(), "fake Codex process never started"
        assert child_pid_file.exists(), "fake Codex child never started"
        model_pid = int(pid_file.read_text(encoding="utf-8"))
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        running.terminate()
        stdout, stderr = running.communicate(timeout=10)
        assert running.returncode == 130, (stdout, stderr)
        assert "INTERRUPTED" in save.read_text(encoding="utf-8")
        assert_process_gone(model_pid)
        assert_process_gone(child_pid)

        # Regression: a supervisor stopping the job signals the whole process
        # tree, so the provider can die before the runner sees its own SIGTERM.
        # The runner must not answer that death with a recovery call, which
        # would start after the supervisor enumerated the tree and be orphaned.
        save = root / "tree-kill.md"
        progress = root / "tree-kill.progress.md"
        pid_file = root / "tree-kill-model.pid"
        ready_file = root / "tree-kill-model.ready"
        call_log = root / "tree-kill-calls.jsonl"
        env = dict(os.environ)
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_CLAUDE_MODE"] = "die_on_term"
        env["FAKE_CLAUDE_PID_FILE"] = str(pid_file)
        env["FAKE_CLAUDE_READY_FILE"] = str(ready_file)
        env["FAKE_CLAUDE_CALL_LOG"] = str(call_log)
        command = [
            str(RUNNER), "--quiet", "--max-rounds", "1", "--no-synthesize",
            "--claude-turn-timeout", "30", "--claude-recovery-timeout", "30",
            "--save", str(save), "--progress", str(progress), "process-tree stop",
        ]
        running = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(100):
            if ready_file.exists() or running.poll() is not None:
                break
            time.sleep(0.05)
        assert ready_file.exists(), "fake Claude never became ready"
        model_pid = int(pid_file.read_text(encoding="utf-8"))
        os.kill(model_pid, signal.SIGTERM)  # provider first, as in a tree kill
        time.sleep(0.3)  # runner's copy of the signal arrives a little later
        running.terminate()
        stdout, stderr = running.communicate(timeout=15)
        assert running.returncode == 130, (stdout, stderr)
        tree_progress = progress.read_text(encoding="utf-8")
        assert "invalid report" not in tree_progress, tree_progress
        assert "recovery: yes" not in tree_progress, tree_progress
        calls = [json.loads(line) for line in call_log.read_text().splitlines() if line]
        assert len(calls) == 1 and not calls[0]["recovery"], calls
        assert "INTERRUPTED" in save.read_text(encoding="utf-8")
        assert_process_gone(model_pid)

    for helper in ("test_hardening.py", "test_lifecycle.py"):
        subprocess.run([sys.executable, str(Path(__file__).with_name(helper)), "-q"], check=True)
    print("PASS: consensus skill offline acceptance tests (including hardening and lifecycle)")


if __name__ == "__main__":
    main()
