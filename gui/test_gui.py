#!/usr/bin/env python3
"""Offline tests for the consensus GUI server, using a fake runner."""
import base64
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

GUI = Path(__file__).with_name("consensus_gui.py")

FAKE_RUNNER = r'''
import json, os, signal, sys, time
if "--resolve-models" in sys.argv:
    print(json.dumps({"models": {
        "claude": {"model": "fake-claude", "effort": "xhigh"},
        "openai": {"model": "fake-codex", "effort": "xhigh"}}}))
    sys.exit(0)
args = sys.argv[1:]
save = args[args.index("--save") + 1]
question = sys.stdin.read()
with open(os.path.join(os.getcwd(), "fake-call.json"), "w") as fh:
    json.dump({"argv": args, "stdin": question,
               "env": {k: v for k, v in os.environ.items() if k.startswith("CONSENSUS_")}}, fh)
def outcome(text):
    with open(save, "w") as fh:
        fh.write("# Consensus debate\n\n## Question\n" + question + "\n## Outcome\n" + text + "\n\n")
        if text == "CONSENSUS REACHED":
            fh.write("## Agreed answer\n## Answer\nYes.\n\n## Bottom line\n2 is prime.\n\n"
                     "## Claude  (verdict: AGREE, 3s)\nturn text\n\n")
if "hang" in question:
    signal.signal(signal.SIGTERM, lambda *a: (outcome("INTERRUPTED"), sys.exit(130)))
    print("waiting", flush=True)
    while True:
        time.sleep(0.05)
def journal(name, events, chunk=37):
    # Like the runner: timestamped stdout read chunks that split events anywhere.
    text = "".join(json.dumps(e) + "\n" for e in events)
    with open(os.path.join(os.path.dirname(save), "progress.md." + name + "-stream.jsonl"), "w") as fh:
        fh.write(json.dumps({"timestamp": "2026-10-08T10:00:00", "stream": "stderr", "line": "noise"}) + "\n")
        for i in range(0, len(text), chunk):
            fh.write(json.dumps({"timestamp": "2026-10-08T10:00:0%d" % (1 if name == "claude" else 5),
                                 "stream": "stdout", "line": text[i:i + chunk]}) + "\n")
delta = lambda i, d: {"type": "stream_event", "event": {"type": "content_block_delta", "index": i, "delta": d}}
journal("claude", [
    {"type": "system", "subtype": "init", "model": "claude-opus-5-5"},
    {"type": "stream_event", "event": {"type": "message_start"}},
    {"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}},
    delta(0, {"type": "thinking_delta", "thinking": "Consider 2."}),
    {"type": "stream_event", "event": {"type": "content_block_start", "index": 1,
     "content_block": {"type": "tool_use", "id": "tu1", "name": "Bash"}}},
    delta(1, {"type": "input_json_delta", "partial_json": '{"command": "fac'}),
    delta(1, {"type": "input_json_delta", "partial_json": 'tor 2"}'}),
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "Bash",
                                                   "input": {"command": "factor 2"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "2: 2"}]}},
    {"type": "stream_event", "parent_tool_use_id": "agent1", "event": {"type": "content_block_delta",
     "index": 0, "delta": {"type": "text_delta", "text": "SUBAGENT TEXT"}}},
    {"type": "stream_event", "event": {"type": "message_start"}},
    {"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}},
    delta(0, {"type": "text_delta", "text": "Yes, 2 is "}),
    delta(0, {"type": "text_delta", "text": "prime."}),
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Yes, 2 is prime."}]}},
    {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 4000, "result": "x"},
])
journal("codex", [
    {"type": "thread.started", "thread_id": "t"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "Check it."}},
    {"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "python3 -c 1",
                                      "aggregated_output": "", "exit_code": None}},
    {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": "python3 -c 1",
                                        "aggregated_output": "ok", "exit_code": 0}},
    {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": "Agreed."}},
    {"type": "turn.completed", "usage": {"output_tokens": 1234}},
])
outcome("CONSENSUS REACHED")
print("done", flush=True)
'''

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys, time
prompt = sys.stdin.read()
with open("fake-claude-call.json", "w") as fh:
    json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt, "pid": os.getpid(),
               "effort_env": os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")}, fh)
if "stream-json" in sys.argv:  # a proof-craft call
    task = prompt.rsplit("<task>", 1)[1]
    emit = lambda e: print(json.dumps(e), flush=True)
    emit({"type": "system", "subtype": "init", "model": "fake-opus"})
    if "please hang" in task:
        while True:
            time.sleep(0.05)
    if "please fail" in task:
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
              "result": "model overloaded"})
        sys.exit(1)
    delta = lambda t: {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                       "delta": {"type": "text_delta", "text": t}}}
    emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                            "content_block": {"type": "text"}}})
    emit(delta("**Proof.** Let $n$ be odd. "))
    emit(delta("Then 8 divides $n^2-1$. ▪"))
    print("warning on stderr", file=sys.stderr, flush=True)
    emit({"type": "result", "subtype": "success", "is_error": False, "parent_tool_use_id": "sub",
          "result": "SUBAGENT RESULT"})
    emit({"type": "result", "subtype": "success", "is_error": False, "duration_ms": 2000,
          "result": "**Proof.** Let $n$ be odd. Then 8 divides $n^2-1$. ▪"})
    sys.exit(0)
pdf = open("source.pdf", "rb").read()
if b"slow" in pdf:
    time.sleep(600)
if b"fail" in pdf:
    print(json.dumps({"type": "result", "is_error": True, "result": "model refused"}))
    sys.exit(1)
for n, body in ((10, "[page 10] last"), (2, "[page 2] middle"), (1, "[page 1] first $x^2$")):
    with open(f"parts/part-{n:03d}.txt", "w") as fh:
        fh.write(body + "\n")
print(json.dumps({"type": "result", "is_error": False, "result": "DONE"}))
'''


FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, sys
prompt = sys.stdin.read()
argv = sys.argv[1:]
with open("fake-codex-call.json", "w") as fh:
    json.dump({"argv": argv, "cwd": os.getcwd(), "prompt": prompt}, fh)
emit = lambda e: print(json.dumps(e), flush=True)
emit({"type": "thread.started", "thread_id": "t1"})
emit({"type": "turn.started"})
emit({"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "Try n=1."}})
if "please fail" in prompt.rsplit("<task>", 1)[1]:
    emit({"type": "turn.failed", "error": {"message": "quota exceeded"}})
    sys.exit(1)
emit({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "Counterexample: n=1."}})
emit({"type": "turn.completed", "usage": {"output_tokens": 77}})
with open(argv[argv.index("-o") + 1], "w") as fh:
    fh.write("The claim is false. **Counterexample.** $n = 1$.\n")
'''


def load_gui():
    import importlib.util
    spec = importlib.util.spec_from_file_location("consensus_gui", GUI)
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)
    return gui


def make_proof_craft(root):
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: proof-craft\n---\nSKILL-MARKER\n")
    (root / "REFERENCE.md").write_text("REFERENCE-MARKER\n")
    (root / "references" / "finite-reductions.md").write_text("FINITE-MARKER\n")
    return root


def proof_unit_tests(tmp, runner):
    """Result parsing, skill discovery, and status recovery without a server."""
    gui = load_gui()
    gui.RUNNER = runner
    ok = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "P."})
    sub = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "parent_tool_use_id": "x", "result": "SUB"})
    assert gui.claude_result([ok, sub]) == ("P.", None)
    assert gui.claude_result([sub])[0] is None
    assert gui.claude_result([])[1] == "Claude ended without a result."
    bad = json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "nope"})
    assert "nope" in gui.claude_result([ok, bad])[1]
    out = tmp / "codex-out.txt"
    out.write_text("Q.")
    done = json.dumps({"type": "turn.completed"})
    assert gui.codex_result([done], out) == ("Q.", None)
    assert gui.codex_result([], out)[0] is None
    assert "boom" in gui.codex_result([json.dumps({"type": "error", "message": "boom"}), done], out)[1]
    assert gui.codex_result([done], tmp / "missing.txt")[0] is None

    skill = make_proof_craft(tmp / "unit-skill")
    assert gui.find_proof_craft(str(skill)) == skill.resolve()
    assert gui.find_proof_craft(str(tmp / "nowhere")) is None
    files = gui.proof_craft_files(skill)
    assert [n for n, _ in files] == ["SKILL.md", "REFERENCE.md", "references/finite-reductions.md"]
    prompt = gui.proof_prompt(files, "Prove it.", [("a.txt", "ctx")])
    assert prompt.index("REFERENCE-MARKER") < prompt.index('<context file="a.txt">') < prompt.index("<task>\nProve it.")
    assert "Do not invoke the consensus skill" in prompt

    app = gui.App(tmp / "unit-runs", str(tmp / "nowhere"))
    assert app.environment()["proof_craft"] is None
    try:
        app.start_proof({"question": "Prove it.", "model": "m", "effort": "high"})
        raise AssertionError("started without proof-craft")
    except gui.BadRequest as error:
        assert "not installed" in str(error)
    assert not any((tmp / "unit-runs").iterdir())

    # A proof that finished while the GUI was closed is read from the worker's status file.
    run_dir = tmp / "unit-runs" / "20261015-120000-abcdef"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"id": run_dir.name, "kind": "proof", "pid": None,
                                                  "exit_code": None, "ended": None}))
    assert app.summary(run_dir)["state"] == "ended"
    (run_dir / "status.json").write_text(json.dumps({"exit_code": 0, "ended": "2026-10-15T12:01:00"}))
    assert app.summary(run_dir)["state"] == "proof"


def request(base, path, token, body=None, headers=None):
    hdrs = {"X-Consensus-Token": token} if token is not None else {}
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, raw.decode()


def wait_for(predicate, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("condition not met in time")


def incremental_feed_test(tmp):
    """Polling mid-write sees partial records later and re-sends grown entries."""
    gui = load_gui()
    run_dir = tmp / "incremental"
    run_dir.mkdir()
    path = run_dir / "progress.md.claude-stream.jsonl"
    feed = gui.LiveFeed(run_dir)
    def record(text):
        return json.dumps({"timestamp": "2026-10-08T10:00:00", "stream": "stdout", "line": text}) + "\n"
    events = "".join(json.dumps(e) + "\n" for e in (
        {"type": "system", "subtype": "init"},
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "text"}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": "Hello"}}}))
    first = record(events)
    with open(path, "w") as fh:
        fh.write(first[:50])  # a journal record caught mid-write
    assert feed.poll(0) == {"version": 0, "entries": []}
    with open(path, "a") as fh:
        fh.write(first[50:])
    out = feed.poll(0)
    assert [e["text"] for e in out["entries"] if e["kind"] == "text"] == ["Hello"], out
    more = json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                         "delta": {"type": "text_delta", "text": ", world"}}})
    with open(path, "a") as fh:
        fh.write(record(more[:20]) + record(more[20:] + "\n"))
    grown = feed.poll(out["version"])
    assert [(e["kind"], e["text"]) for e in grown["entries"]] == [("text", "Hello, world")], grown


def wait_state(base, token, run_id, state):
    return wait_for(lambda: (lambda d: d if d["state"] == state else None)(
        request(base, f"/api/runs/{run_id}", token)[1]))


def proof_server_tests(base, token, runs_dir, skill):
    """Proof construction runs one provider call per run in a detached worker."""
    state = request(base, "/api/state", token)[1]
    assert state["proof_craft"] == str(skill.resolve()), state["proof_craft"]
    before = set(runs_dir.iterdir())
    for bad in ({"mode": "proof", "question": " "},
                {"mode": "proof", "question": "q", "writer": "gpt"},
                {"mode": "proof", "question": "q", "model": "x; rm -rf ~"},
                {"mode": "proof", "question": "q", "effort": "--max"},
                {"mode": "proof", "question": "q", "contexts": "nope"}):
        assert request(base, "/api/runs", token, bad)[0] == 400, bad
    assert set(runs_dir.iterdir()) == before

    # Claude writes a proof with the default model; the skill and context reach it via stdin.
    task = "Prove that 8 divides n^2-1 for odd n.\nThe reader knows congruences."
    status, run = request(base, "/api/runs", token, {
        "mode": "proof", "question": task, "writer": "claude",
        "contexts": [{"name": "notes.txt", "text": "CONTEXT-MARKER"}]})
    assert status == 200, run
    assert run["kind"] == "proof" and run["model"] == "fake-claude" and run["effort"] == "xhigh", run
    detail = wait_state(base, token, run["id"], "proof")
    assert detail["proof"] == "**Proof.** Let $n$ be odd. Then 8 divides $n^2-1$. ▪\n", detail
    assert detail["exit_code"] == 0 and detail["title"] == "Prove that 8 divides n^2-1 for odd n."
    assert "transcript" not in detail and "agreed_answer" not in detail
    run_dir = runs_dir / run["id"]
    call = json.loads((run_dir / "fake-claude-call.json").read_text())
    argv = call["argv"]
    assert Path(call["cwd"]).resolve() == run_dir.resolve()
    assert "-p" in argv and argv[argv.index("--output-format") + 1] == "stream-json", argv
    assert argv[argv.index("--model") + 1] == "fake-claude" and argv[argv.index("--effort") + 1] == "xhigh"
    assert "--disable-slash-commands" in argv and argv[argv.index("--disallowed-tools") + 1] == "Agent"
    assert call["effort_env"] == "xhigh"
    assert "8 divides" not in " ".join(argv), "task leaked into argv"
    for marker in ("SKILL-MARKER", "REFERENCE-MARKER", "FINITE-MARKER", "CONTEXT-MARKER",
                   "The reader knows congruences.", "Do not invoke the consensus skill"):
        assert marker in call["prompt"], marker
    assert call["prompt"] == (run_dir / "prompt.txt").read_text()
    meta = json.loads((run_dir / "run.json").read_text())
    assert meta["proof_craft_files"] == ["SKILL.md", "REFERENCE.md", "references/finite-reductions.md"]
    assert len(meta["reference_sha256"]) == 64
    for name in ("question.txt", "prompt.txt", "proof.md", "status.json", "console.log", "run.json",
                 "context/notes.txt", "proof.claude-stream.jsonl"):
        assert stat.S_IMODE((run_dir / name).stat().st_mode) == 0o600, name
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    records = [json.loads(l) for l in (run_dir / "proof.claude-stream.jsonl").read_text().splitlines()]
    assert {r["stream"] for r in records} == {"stdout", "stderr"}
    assert all(set(r) == {"timestamp", "stream", "line"} for r in records)
    feed = request(base, f"/api/runs/{run['id']}/live?since=0", token)[1]
    shown = [(e["who"], e["kind"], e.get("text")) for e in feed["entries"]]
    assert shown[0] == ("Claude", "call", None) and feed["entries"][0]["model"] == "fake-opus", shown
    assert ("Claude", "text", "**Proof.** Let $n$ be odd. Then 8 divides $n^2-1$. ▪") in shown, shown
    assert ("Claude", "status", "Call finished in 2s") in shown, shown

    # Codex with an explicit model: the final message comes from its -o file.
    status, run = request(base, "/api/runs", token, {
        "mode": "proof", "question": "Prove every n is even.", "writer": "codex",
        "model": "gpt-test", "effort": "high"})
    assert status == 200, run
    detail = wait_state(base, token, run["id"], "proof")
    assert detail["proof"] == "The claim is false. **Counterexample.** $n = 1$.\n", detail
    run_dir = runs_dir / run["id"]
    argv = json.loads((run_dir / "fake-codex-call.json").read_text())["argv"]
    assert argv[:2] == ["exec", "--skip-git-repo-check"] and "--json" in argv, argv
    assert argv[argv.index("--model") + 1] == "gpt-test", argv
    assert argv[argv.index("--config") + 1] == 'model_reasoning_effort="high"', argv
    assert argv[argv.index("-o") + 1] == str(run_dir / "codex-last-message.txt"), argv
    assert stat.S_IMODE((run_dir / "codex-last-message.txt").stat().st_mode) == 0o600
    feed = request(base, f"/api/runs/{run['id']}/live?since=0", token)[1]
    assert [(e["who"], e["kind"]) for e in feed["entries"]][:3] == [
        ("Codex", "call"), ("Codex", "thinking"), ("Codex", "text")], feed

    # Provider failures are reported, with no proof file.
    for writer, reason in (("claude", "model overloaded"), ("codex", "quota exceeded")):
        status, run = request(base, "/api/runs", token,
                              {"mode": "proof", "question": "please fail", "writer": writer})
        detail = wait_state(base, token, run["id"], "failed")
        assert detail["exit_code"] == 1 and reason in detail["error"], detail
        assert reason in detail["console"] and detail["proof"] is None
        assert not (runs_dir / run["id"] / "proof.md").exists()

    # Stop terminates the provider's process group and is reported as stopped.
    status, run = request(base, "/api/runs", token, {"mode": "proof", "question": "please hang"})
    run_dir = runs_dir / run["id"]
    wait_for(lambda: (run_dir / "fake-claude-call.json").exists())
    assert request(base, f"/api/runs/{run['id']}", token)[1]["state"] == "running"
    assert request(base, f"/api/runs/{run['id']}/stop", token, {})[0] == 200
    detail = wait_state(base, token, run["id"], "interrupted")
    assert detail["exit_code"] == 130 and detail["stop_requested"] and detail["proof"] is None
    pid = json.loads((run_dir / "fake-claude-call.json").read_text())["pid"]
    wait_for(lambda: subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                    capture_output=True, text=True).stdout.strip() in ("", "Z"))
    assert json.loads((run_dir / "status.json").read_text())["exit_code"] == 130
    assert request(base, f"/api/runs/{run['id']}/stop", token, {})[0] == 400

    kinds = [r["kind"] for r in request(base, "/api/state", token)[1]["runs"]]
    assert kinds.count("proof") == 5 and kinds.count("debate") == 2, kinds


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        incremental_feed_test(tmp)
        runner = tmp / "fake_runner.py"
        runner.write_text(FAKE_RUNNER)
        proof_unit_tests(tmp, runner)
        skill = make_proof_craft(tmp / "proof-craft")
        runs_dir = tmp / "runs"
        bindir = tmp / "bin"
        bindir.mkdir()
        for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
            (bindir / name).write_text(body)
            (bindir / name).chmod(0o755)
        env = {**{k: v for k, v in os.environ.items() if not k.startswith("CONSENSUS_")},
               "CONSENSUS_GUI_RUNNER": str(runner),
               "CONSENSUS_GUI_PROOF_CRAFT": str(skill),
               "PAPERCLIP_API_KEY": "gxl_gui_test",
               "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
        server = subprocess.Popen(
            [sys.executable, str(GUI), "--port", "0", "--no-browser", "--runs-dir", str(runs_dir)],
            stdout=subprocess.PIPE, text=True, env=env)
        try:
            url = server.stdout.readline().split(": ", 1)[1].strip()
            base, token = url.split("/?t=")

            # Authorization: token, Host, and Origin are all required.
            assert request(base, "/api/state", None)[0] == 403
            assert request(base, "/api/state", "wrong")[0] == 403
            assert request(base, "/api/state", token,
                           headers={"Origin": "http://evil.example"})[0] == 403
            assert request(base, "/api/runs", token, {"question": "x"},
                           headers={"Host": "evil.example"})[0] == 403
            with urllib.request.urlopen(url, timeout=10) as res:
                assert b"<title>Consensus</title>" in res.read()
            try:
                urllib.request.urlopen(base + "/", timeout=10)
                raise AssertionError("page served without token")
            except urllib.error.HTTPError as error:
                assert error.code == 403

            state = wait_for(lambda: (lambda s: s if s["models"] else None)(
                request(base, "/api/state", token)[1]))
            assert state["models"]["claude"]["model"] == "fake-claude", state
            claude_choices = [c["model"] for c in state["choices"]["claude"]["models"]]
            assert "claude-opus-5-5" in claude_choices and "claude-sonnet-5-5" in claude_choices
            assert "xhigh" in state["choices"]["codex"]["efforts"]
            assert stat.S_IMODE(runs_dir.stat().st_mode) == 0o700

            # Validation rejects bad input without creating a run.
            assert request(base, "/api/runs", token, {"question": "  "})[0] == 400
            assert request(base, "/api/runs", token, {"question": "q", "max_rounds": 0})[0] == 400
            assert request(base, "/api/runs", token, {"question": "q", "first": "gpt"})[0] == 400
            assert request(base, "/api/runs", token,
                           {"question": "q", "claude_model": "x; rm -rf ~"})[0] == 400
            assert request(base, "/api/runs", token,
                           {"question": "q", "codex_effort": "--very-high"})[0] == 400
            # Literature search needs a sub-agent and the GUI's Paperclip key.
            assert request(base, "/api/runs", token,
                           {"question": "q", "literature": True, "subagents": 0})[0] == 400
            assert state["paperclip_key"] is True
            assert not any(runs_dir.iterdir())

            # A completed debate: argv, stdin delivery, contexts, private modes, outcome.
            status, run = request(base, "/api/runs", token, {
                "question": "Is 2 prime?\nSecond line", "max_rounds": 2, "first": "codex",
                "subagents": 1, "synthesize": False, "literature": True,
                "claude_model": "claude-sonnet-5-5", "claude_effort": "high",
                "codex_model": "fake-codex", "codex_effort": "medium",
                "contexts": [{"name": "../../evil.txt", "text": "ctx one"},
                             {"name": "evil.txt", "text": "ctx two"}]})
            assert status == 200, run
            assert run["title"] == "Is 2 prime?"
            run_dir = runs_dir / run["id"]
            detail = wait_for(lambda: (lambda d: d if d["state"] == "consensus" else None)(
                request(base, f"/api/runs/{run['id']}", token)[1]))
            assert detail["outcome"] == "CONSENSUS REACHED" and detail["exit_code"] == 0
            # The agreed answer keeps its own headings and stops before the turns.
            assert detail["agreed_answer"] == "## Answer\nYes.\n\n## Bottom line\n2 is prime.", detail

            call = json.loads((run_dir / "fake-call.json").read_text())
            argv = call["argv"]
            assert call["stdin"] == "Is 2 prime?\nSecond line\n"
            # Only differences from the runner's defaults become explicit overrides.
            assert call["env"] == {"CONSENSUS_CLAUDE_MODEL": "claude-sonnet-5-5",
                                   "CONSENSUS_CLAUDE_EFFORT": "high",
                                   "CONSENSUS_CODEX_REASONING_EFFORT": "medium"}, call["env"]
            assert (detail["claude_model"], detail["claude_effort"],
                    detail["codex_model"], detail["codex_effort"]) == (
                "claude-sonnet-5-5", "high", "fake-codex", "medium"), detail
            assert "Is 2 prime?" not in " ".join(argv), "question leaked into argv"
            for flag, value in (("--max-rounds", "2"), ("--first", "codex"), ("--subagents", "1"),
                                ("--save", str(run_dir / "transcript.md")),
                                ("--progress", str(run_dir / "progress.md"))):
                assert argv[argv.index(flag) + 1] == value, argv
            assert "--quiet" in argv and "--no-synthesize" in argv and "--literature" in argv
            assert detail["literature"] is True
            contexts = [argv[i + 1] for i, a in enumerate(argv) if a == "--context"]
            assert contexts == [str(run_dir / "context" / "evil.txt"),
                                str(run_dir / "context" / "evil-2.txt")], contexts
            assert (run_dir / "context" / "evil-2.txt").read_text() == "ctx two"
            for name in ("question.txt", "console.log", "run.json",
                         "context/evil.txt", "context/evil-2.txt"):
                assert stat.S_IMODE((run_dir / name).stat().st_mode) == 0o600, name
            assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
            assert "done" in detail["console"]
            assert not list(run_dir.glob(".run.json.*")), "temporary metadata left behind"

            # The live feed rebuilds both journals into display entries.
            feed = request(base, f"/api/runs/{run['id']}/live?since=0", token)[1]
            shown = [(e["who"], e["kind"], e.get("name"), e.get("text"), e.get("output"))
                     for e in feed["entries"]]
            assert shown == [
                ("Claude", "call", None, None, None),
                ("Claude", "thinking", None, "Consider 2.", None),
                ("Claude", "tool", "Bash", "factor 2", "2: 2"),
                ("Claude", "text", None, "Yes, 2 is prime.", None),
                ("Claude", "status", None, "Call finished in 4s", None),
                ("Codex", "call", None, None, None),
                ("Codex", "thinking", None, "Check it.", None),
                ("Codex", "tool", "shell", "python3 -c 1", "ok"),
                ("Codex", "text", None, "Agreed.", None),
                ("Codex", "status", None, "Turn finished · 1,234 output tokens", None),
            ], shown
            assert feed["entries"][0]["model"] == "claude-opus-5-5"
            assert all("input" not in e for e in feed["entries"])
            again = request(base, f"/api/runs/{run['id']}/live?since={feed['version']}", token)[1]
            assert again == {"version": feed["version"], "entries": []}, again
            assert request(base, f"/api/runs/{run['id']}/live?since=0", token, headers={})[0] == 200
            assert request(base, f"/api/runs/{run['id']}/live", None)[0] == 403

            # Path traversal in run ids is refused.
            assert request(base, "/api/runs/..%2F..%2Fetc", token)[0] == 404
            assert request(base, "/api/runs/20990101-000000-abcdef", token)[0] == 404

            # Stop sends SIGTERM to the runner, which exits 130 and is reported as stopped.
            status, run = request(base, "/api/runs", token, {"question": "please hang"})
            assert status == 200, run
            assert run["claude_model"] == "fake-claude" and run["codex_effort"] == "xhigh", run
            wait_for(lambda: "waiting" in (request(base, f"/api/runs/{run['id']}", token)[1]
                                           .get("console") or ""))
            assert request(base, f"/api/runs/{run['id']}", token)[1]["state"] == "running"
            assert request(base, f"/api/runs/{run['id']}/stop", token, {})[0] == 200
            detail = wait_for(lambda: (lambda d: d if d["state"] == "interrupted" else None)(
                request(base, f"/api/runs/{run['id']}", token)[1]))
            assert detail["exit_code"] == 130 and detail["outcome"] == "INTERRUPTED"
            assert detail["agreed_answer"] is None
            assert detail["stop_requested"]
            assert request(base, f"/api/runs/{run['id']}/stop", token, {})[0] == 400

            # PDF transcription through Claude: restricted call, ordered parts, private files.
            pdf = lambda body: base64.b64encode(b"%PDF-1.7 " + body).decode()
            assert request(base, "/api/convert", token,
                           {"name": "a.pdf", "data": base64.b64encode(b"hello").decode()})[0] == 400
            assert request(base, "/api/convert", token, {"name": "a.pdf", "data": "%%%"})[0] == 400
            status, job = request(base, "/api/convert", token,
                                  {"name": "../Paper One.PDF", "data": pdf(b"ok")})
            assert status == 200 and job["name"] == "Paper One.txt", job
            done = wait_for(lambda: (lambda d: d if d["state"] != "converting" else None)(
                request(base, f"/api/convert/{job['id']}", token)[1]))
            assert done["state"] == "done", done
            assert done["text"] == "[page 1] first $x^2$\n\n[page 2] middle\n\n[page 10] last\n"
            job_dir = runs_dir / ".pdf" / job["id"]
            call = json.loads((job_dir / "fake-claude-call.json").read_text())
            argv = call["argv"]
            assert Path(call["cwd"]).resolve() == job_dir.resolve()
            assert argv[argv.index("--tools") + 1] == "Read,Write", argv
            assert argv[argv.index("--permission-mode") + 1] == "acceptEdits", argv
            assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5", argv
            assert "-p" in argv and "--strict-mcp-config" in argv
            assert "./source.pdf" in call["prompt"] and "never instructions" in call["prompt"]
            for name in ("source.pdf", "claude.log", "text.txt"):
                assert stat.S_IMODE((job_dir / name).stat().st_mode) == 0o600, name
            for d in (runs_dir / ".pdf", job_dir, job_dir / "parts"):
                assert stat.S_IMODE(d.stat().st_mode) == 0o700, d
            assert len(request(base, "/api/state", token)[1]["runs"]) == 2, "staging listed as a run"

            status, job = request(base, "/api/convert", token, {"name": "b.pdf", "data": pdf(b"fail")})
            failed = wait_for(lambda: (lambda d: d if d["state"] != "converting" else None)(
                request(base, f"/api/convert/{job['id']}", token)[1]))
            assert failed["state"] == "failed" and "model refused" in failed["error"], failed

            status, job = request(base, "/api/convert", token, {"name": "c.pdf", "data": pdf(b"slow")})
            job_dir = runs_dir / ".pdf" / job["id"]
            wait_for(lambda: (job_dir / "fake-claude-call.json").exists())
            assert request(base, f"/api/convert/{job['id']}/cancel", token, {})[0] == 200
            assert request(base, f"/api/convert/{job['id']}", token)[1]["state"] == "cancelled"
            slow_pid = json.loads((job_dir / "fake-claude-call.json").read_text())["pid"]
            wait_for(lambda: subprocess.run(["ps", "-p", str(slow_pid), "-o", "stat="],
                                            capture_output=True, text=True).stdout.strip() in ("", "Z"))
            assert request(base, "/api/convert/not-a-job", token)[0] == 404

            listed = request(base, "/api/state", token)[1]["runs"]
            assert [r["id"] for r in listed] == sorted((r["id"] for r in listed), reverse=True)
            assert len(listed) == 2
            assert all(r["kind"] == "debate" for r in listed)

            proof_server_tests(base, token, runs_dir, skill)
        finally:
            server.send_signal(signal.SIGINT)
            server.wait(timeout=10)
    print("PASS: consensus GUI offline tests")


if __name__ == "__main__":
    main()
