import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
import claude_parallel as p  # noqa: E402

# Поддельный gh: пишет вызовы в calls.log, отвечает по файлам в FAKE_GH_STATE и «сливает» PR прямо в голый репозиторий
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
state = os.environ["FAKE_GH_STATE"]
args = sys.argv[1:]
with open(os.path.join(state, "calls.log"), "a") as f:
    f.write(json.dumps(args) + "\n")
def read(name, default=""):
    try:
        with open(os.path.join(state, name)) as f:
            return f.read()
    except OSError:
        return default
def arg(flag):
    return args[args.index(flag) + 1] if flag in args else None
cmd = args[:2]
if cmd == ["pr", "list"]:
    print(read("open.json", "[]"))
elif cmd == ["pr", "create"]:
    with open(os.path.join(state, "head"), "w") as f:
        f.write(arg("--head"))
    with open(os.path.join(state, "open.json"), "w") as f:
        json.dump([{"number": 7, "url": "https://github.com/acme/demo/pull/7"}], f)
    print("https://github.com/acme/demo/pull/7")
elif cmd == ["pr", "checks"]:
    if "--watch" in args:
        sys.exit(int(read("watch_rc", "0")))
    checks = read("checks.json")
    if not checks:
        sys.stderr.write("no checks reported on the 'x' branch\n")
        sys.exit(1)
    print(checks)
elif cmd == ["pr", "merge"]:
    bare, head = os.environ["FAKE_GH_BARE"], read("head") or json.loads(read("open.json"))[0]["head"]
    git = ["git", "--git-dir", bare]
    subprocess.run(git + ["update-ref", "refs/heads/main", "refs/heads/" + head], check=True)
    subprocess.run(git + ["update-ref", "-d", "refs/heads/" + head], check=True)
else:
    sys.exit(3)
'''


def sh(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=True).stdout.strip()


class ParallelPr(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.env = dict(os.environ)
        gitconf = os.path.join(self.tmp, "gitconfig")
        open(gitconf, "w").close()
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "gh"), "w") as f:
            f.write(FAKE_GH)
        os.chmod(os.path.join(bindir, "gh"), 0o755)
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        self.bare = os.path.join(self.tmp, "origin.git")
        self.dev = os.path.join(self.tmp, "dev")
        self.root = os.path.join(self.dev, "demo")
        os.environ.update({
            "GIT_CONFIG_GLOBAL": gitconf, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "PATH": bindir + os.pathsep + os.environ["PATH"],
            "MNRH_PROJECTS": self.dev, "FAKE_GH_STATE": self.state, "FAKE_GH_BARE": self.bare})
        self.claude_home = p.CLAUDE_HOME
        p.CLAUDE_HOME = os.path.join(self.tmp, "claude")
        os.makedirs(os.path.join(p.CLAUDE_HOME, "sessions"))
        self.waits = p.CHECKS_APPEAR, p.CHECKS_APPEAR_ACTIONS
        p.CHECKS_APPEAR = p.CHECKS_APPEAR_ACTIONS = 0

        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.bare], check=True)
        os.makedirs(self.dev)
        subprocess.run(["git", "clone", "-q", self.bare, self.root], check=True, capture_output=True)
        sh(self.root, "symbolic-ref", "HEAD", "refs/heads/main")
        self.commit(self.root, "README", "hi\n")
        sh(self.root, "push", "-q", "-u", "origin", "main")
        # remote похож на GitHub, а ходит в локальный голый репозиторий
        sh(self.root, "config", "remote.origin.url", "https://github.com/acme/demo.git")
        sh(self.root, "config", f"url.{self.bare}.insteadOf", "https://github.com/acme/demo.git")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        p.CLAUDE_HOME = self.claude_home
        p.CHECKS_APPEAR, p.CHECKS_APPEAR_ACTIONS = self.waits
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, cwd, name, text):
        with open(os.path.join(cwd, name), "w") as f:
            f.write(text)
        sh(cwd, "add", name)
        sh(cwd, "commit", "-q", "-m", f"add {name}")

    def task(self, name="parallel/fix"):
        path = os.path.join(p.worktree_root(self.root), name.replace("/", "-"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sh(self.root, "worktree", "add", "-q", "-b", name, path, "HEAD")
        sh(self.root, "config", f"branch.{name}.mnrhBase", "main")
        self.commit(path, "fix.txt", "fixed\n")
        return path

    def calls(self):
        try:
            with open(os.path.join(self.state, "calls.log")) as f:
                return [json.loads(line) for line in f]
        except OSError:
            return []

    def merges(self):
        return [c for c in self.calls() if c[:2] == ["pr", "merge"]]

    def put(self, name, text):
        with open(os.path.join(self.state, name), "w") as f:
            f.write(text)

    def run_pr(self, merge=True):
        log = []
        text, bad = p.pr("fix", self.root, merge=merge, log=log.append)
        return "\n".join(log + [text]), bad

    def test_no_merge_pushes_and_opens_pr(self):
        path = self.task()
        text, bad = self.run_pr(merge=False)
        self.assertFalse(bad, text)
        self.assertIn("PR #7", text)
        self.assertTrue(sh(self.bare, "rev-parse", "--verify", "refs/heads/parallel/fix"))
        create = [c for c in self.calls() if c[:2] == ["pr", "create"]][0]
        self.assertEqual(create[create.index("--base") + 1], "main")
        self.assertIn("--fill", create)
        self.assertEqual(self.merges(), [])
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(sh(self.bare, "tag"), "")

    def test_no_checks_merges_with_rebase_and_cleans_up(self):
        path = self.task()
        text, bad = self.run_pr()
        self.assertFalse(bad, text)
        merge = self.merges()[0]
        self.assertIn("--rebase", merge)
        self.assertIn("--delete-branch", merge)
        self.assertIn("-R", merge)  # с -R gh не трогает локальную ветку, её убирает claudemeister
        self.assertFalse(os.path.exists(path))
        self.assertEqual(sh(self.root, "branch", "--list", "parallel/fix"), "")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "fix.txt")), text)
        self.assertIn("Подтянул main", text)
        self.assertEqual(p.entries(self.root), [])

    def test_merge_commits_on_base_choose_merge(self):
        sh(self.root, "checkout", "-q", "-b", "side")
        self.commit(self.root, "side.txt", "s\n")
        sh(self.root, "checkout", "-q", "main")
        sh(self.root, "merge", "-q", "--no-ff", "--no-edit", "side")
        sh(self.root, "push", "-q", "origin", "main")
        self.task()
        text, bad = self.run_pr()
        self.assertFalse(bad, text)
        self.assertIn("--merge", self.merges()[0])

    def test_failed_ci_keeps_worktree(self):
        path = self.task()
        self.put("checks.json", json.dumps([{"name": "build", "bucket": "fail", "link": "https://ci/1"},
                                            {"name": "lint", "bucket": "pass", "link": ""}]))
        self.put("watch_rc", "1")
        text, bad = self.run_pr()
        self.assertTrue(bad)
        self.assertIn("build: fail https://ci/1", text)
        self.assertNotIn("lint", text)
        self.assertEqual(self.merges(), [])
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(sh(self.root, "branch", "--list", "parallel/fix"))

    def test_passing_ci_merges(self):
        self.task()
        self.put("checks.json", json.dumps([{"name": "build", "bucket": "pass", "link": ""}]))
        text, bad = self.run_pr()
        self.assertFalse(bad, text)
        self.assertIn("CI прошёл", text)
        self.assertTrue(any("--watch" in c for c in self.calls()))
        self.assertEqual(len(self.merges()), 1)

    def test_open_pr_is_reused(self):
        self.task()
        self.put("open.json", json.dumps([{"number": 3, "url": "https://github.com/acme/demo/pull/3",
                                           "head": "parallel/fix"}]))
        text, bad = self.run_pr(merge=False)
        self.assertFalse(bad, text)
        self.assertIn("PR #3", text)
        self.assertFalse([c for c in self.calls() if c[:2] == ["pr", "create"]])

    def test_refuses_dirty_worktree(self):
        path = self.task()
        with open(os.path.join(path, "fix.txt"), "a") as f:
            f.write("more\n")
        text, bad = self.run_pr()
        self.assertTrue(bad)
        self.assertIn("незакоммичено", text)
        self.assertEqual(self.calls(), [])

    def test_refuses_while_claude_runs_there(self):
        path = self.task()
        with open(os.path.join(p.CLAUDE_HOME, "sessions", "1.json"), "w") as f:
            json.dump({"cwd": path, "pid": os.getpid()}, f)
        text, bad = self.run_pr()
        self.assertTrue(bad)
        self.assertIn("работает Claude", text)
        self.assertEqual(self.calls(), [])

    def test_mcp_action_pr(self):
        self.task()
        text, bad = p.handle("parallel_finish", {"branch": "fix", "action": "pr", "no_merge": True,
                                                 "project": self.root})
        self.assertFalse(bad, text)
        self.assertIn("Открыл PR", text)
        self.assertEqual(self.merges(), [])


class GithubRepo(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(p.github_repo("https://github.com/acme/demo.git"), "acme/demo")
        self.assertEqual(p.github_repo("git@github.com:acme/demo.git"), "acme/demo")
        self.assertEqual(p.github_repo("ssh://git@github.com/acme/demo"), "acme/demo")
        self.assertEqual(p.github_repo("https://ghe.corp.example/acme/demo.git"), "ghe.corp.example/acme/demo")
        self.assertIsNone(p.github_repo("/tmp/origin.git"))
        self.assertIsNone(p.github_repo(""))


if __name__ == "__main__":
    unittest.main()
