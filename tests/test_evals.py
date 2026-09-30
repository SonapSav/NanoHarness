"""The eval runner itself, driven by a scripted fake model (the real evals need Ollama)."""
import builtins

from evals import cases, run
from nanoharness import client, config


def script(monkeypatch, *steps):
    steps = iter(steps)
    monkeypatch.setattr(client, "chat", lambda messages, tools=None, on_token=None: next(steps))


def calls(name, **args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def say(text):
    return {"role": "assistant", "content": text}


def case(name):
    return next(c for c in cases.CASES if c.name == name)


def test_a_correct_run_passes_and_is_checked_against_the_world(monkeypatch):
    script(monkeypatch,
           calls("write_file", path="fizzbuzz.py",
                 content="for i in range(1, 16):\n"
                         "    print('FizzBuzz' if i % 15 == 0 else 'Fizz' if i % 3 == 0 "
                         "else 'Buzz' if i % 5 == 0 else i)\n"),
           calls("bash", command="python3 fizzbuzz.py"),
           say("Done."))
    r = run.run_case(case("create_and_run"))
    assert r["passed"], r["checks"]
    assert r["tools"] == {"write_file": 1, "bash": 1}


def test_claiming_success_after_a_denial_fails_the_right_check(monkeypatch):
    script(monkeypatch,
           calls("write_file", path="hello.txt", content="hi"),
           say("Created hello.txt."))                      # a lie: the write was denied
    r = run.run_case(case("respects_denial"))
    assert not r["passed"]
    failed = [c["check"] for c in r["checks"] if not c["ok"]]
    assert failed == ["answer matches /den(y|ied)|not (allowed|permitted|approved)/"]
    assert r["prompts"] == 1                                # the approval was asked, and refused


def test_runner_restores_global_state(monkeypatch):
    before_workdir, before_input = config.WORKDIR, builtins.input
    script(monkeypatch, say("hi"))
    run.run_case(case("no_invented_contents"))
    assert config.WORKDIR == before_workdir and builtins.input is before_input


def test_report_compares_with_a_baseline():
    runs = [{"case": "a", "group": "g", "passed": True, "checks": [], "error": None,
             "tools": {"bash": 1}, "tokens": 100, "seconds": 1.0}] * 2
    summary = run.summarize(runs)
    baseline = {"summary": {"a": {**summary["a"], "passed": 1}}}
    text = run.report(summary, runs, baseline)
    assert "2/2" in text and "1/2+" in text and "total" in text


def rename_run(monkeypatch, rewrite):
    """A run of rename_across_files whose model rewrites every file with `rewrite`."""
    steps = [calls("write_file", path=path, content=rewrite(text))
             for path, text in cases.SHOP.items() if rewrite(text) != text]
    script(monkeypatch, *steps, say("Renamed."))
    r = run.run_case(case("rename_across_files"))
    return {c["check"]: c for c in r["checks"]}, r


def test_rename_fixture_tests_pass_before_the_rename(tmp_path, monkeypatch):
    from nanoharness import tools
    for rel, text in cases.SHOP.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    out = tools.bash("python3 -m unittest discover -s tests -t . -q")
    assert out.startswith("exit code: 0"), out


def test_an_exact_rename_passes(monkeypatch):
    import re
    checks, r = rename_run(monkeypatch, lambda t: re.sub(r"\bcalc_total\b", "order_total", t))
    assert r["passed"], checks


def test_a_blind_substring_replace_breaks_the_lookalike(monkeypatch):
    # Tests still pass (calc_total_weight is renamed consistently), so only the exact check sees it.
    checks, r = rename_run(monkeypatch, lambda t: t.replace("calc_total", "order_total"))
    assert checks["tests pass"]["ok"]
    exact = next(c for name, c in checks.items() if name.startswith("all "))
    assert not exact["ok"] and "order_total_weight" in exact["detail"]


def test_missing_the_string_references_fails_the_tests(monkeypatch):
    import re
    def imports_only(text):    # renames identifiers but not "calc_total" in quotes
        return re.sub(r'(?<!")\bcalc_total\b(?!")', "order_total", text)
    checks, r = rename_run(monkeypatch, imports_only)
    assert not checks["tests pass"]["ok"]
    assert "shop/report.py" in next(c for n, c in checks.items() if n.startswith("all "))["detail"]


def test_host_url_fills_in_scheme_and_port():
    assert run.host_url("100.76.19.74") == "http://100.76.19.74:11434"
    assert run.host_url(" http://box:9999/ ") == "http://box:9999"


def test_worker_tags_each_result_with_its_host_and_survives_a_crash(monkeypatch):
    import queue
    real = run.run_case

    def run_case(c, keep=False):
        if c.name == "precise_edit":
            raise RuntimeError("boom")
        return real(c, keep)

    monkeypatch.setattr(run, "run_case", run_case)
    monkeypatch.setattr(config, "OLLAMA_HOST", config.OLLAMA_HOST)   # worker sets it; restore
    script(monkeypatch, say("No config.yaml here."))
    jobs, results = queue.Queue(), queue.Queue()
    for job in [(0, "no_invented_contents", False), (1, "precise_edit", False), None]:
        jobs.put(job)
    run.worker("http://h1:11434", jobs, results)
    got = dict(results.get() for _ in range(2))
    assert got[0]["host"] == got[1]["host"] == "http://h1:11434"
    assert got[0]["passed"]
    assert not got[1]["passed"] and "RuntimeError: boom" in got[1]["error"]


def test_several_hosts_run_in_worker_processes_and_keep_job_order():
    # Real processes, so no scripted model: closed local ports make every run fail fast.
    hosts = ["http://127.0.0.1:9", "http://127.0.0.1:19"]
    jobs = [(case("no_invented_contents"), i) for i in range(3)] + [(case("precise_edit"), 0)]
    seen = []
    runs = run.run_all(jobs, hosts, keep=False, show=lambda done, i, r: seen.append(i))
    assert [r["case"] for r in runs] == ["no_invented_contents"] * 3 + ["precise_edit"]
    assert all(r["host"] in hosts and "Cannot reach Ollama" in r["error"] for r in runs)
    assert sorted(seen) == [0, 1, 2, 3]


def test_report_shows_a_line_per_host_when_there_are_several():
    runs = [{"case": "a", "group": "g", "passed": p, "checks": [], "error": None, "tools": {},
             "tokens": 1, "seconds": 2.0, "host": h} for p, h in [(True, "h1"), (False, "h2")]]
    text = run.report(run.summarize(runs), runs)
    assert "by host:" in text and "1/1" in text and "0/1" in text
