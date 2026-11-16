"""
.claude/hooks/block-tmp-writes.py 회귀 테스트.

훅을 실제 실행 파일로 돌려, Claude Code 와 Grok CLI 입력 형식 모두에서
scratchpad 밖 임시 경로(/tmp, /var/folders) 쓰기만 막고 정상 작업은 통과시키는지 확인한다.
$TMPDIR 쓰기는 호출자별로 가른다. Claude payload 는 SessionStart 가 Bash TMPDIR 을
루트로 두므로 허용하고, Grok payload 는 훅 환경 TMPDIR 이 ALLOWED_ROOTS 안일 때만 허용한다.
차단 사례 중 일부는 이 프로젝트 작업 중 실제로 일어난 실수다.
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "block-tmp-writes.py"
HEAD_HOOK = Path(__file__).resolve().parent / "fixtures" / "block_tmp_writes_head.py"
SCRATCH = "/private/tmp/claude-501/-Users-ny-Documents-programs-resell-sniper/session/scratchpad"
HOOK_TIMEOUT_SEC = 10
# 앞에 큰따옴표 하나와 이 횟수만큼 붙인 입력(약 99k자).
UNBALANCED_QUOTE_REPEAT = 9000
# redos5 형태: 한 세그먼트에 연산자와 경로가 이 횟수만큼 반복된다.
REDOS_OPERATOR_REPEAT = 3000
SHELL_NEST_DEPTH = 9
HOOK_PERF_MAX_SEC = 1


def load_module(path: Path, name: str):
    """하이픈이 있는 파일을 모듈로 불러온다."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"모듈을 불러오지 못함: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_hook():
    """하이픈이 있는 훅 파일을 모듈로 불러 ALLOWED_ROOTS 를 읽는다."""
    return load_module(HOOK, "block_tmp_writes")


def run_hook_payload(payload: dict, env: dict | None = None, hook: Path = HOOK) -> dict | None:
    """주어진 payload 로 훅을 실행한다. 통과면 None."""
    result = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=HOOK_TIMEOUT_SEC,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(f"훅이 비정상 종료: {result.returncode} {result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def run_hook(
    tool_name: str,
    tool_input: dict,
    grok_style: bool = False,
    env: dict | None = None,
) -> dict | None:
    """훅을 실행해 차단 응답(JSON)을 돌려준다. 통과면 None. env 를 주면 그 환경을 쓴다."""
    if grok_style:
        payload = {"toolName": tool_name, "toolInput": tool_input, "toolInputTruncated": False}
    else:
        payload = {"tool_name": tool_name, "tool_input": tool_input}
    return run_hook_payload(payload, env)


def is_denied(reply: dict | None) -> bool:
    return bool(reply) and reply["hookSpecificOutput"]["permissionDecision"] == "deny"


class BlockedWriteTests(unittest.TestCase):
    def assert_denied(self, tool_name: str, tool_input: dict, grok_style: bool = False) -> None:
        reply = run_hook(tool_name, tool_input, grok_style)
        self.assertTrue(is_denied(reply), f"차단되어야 함: {tool_name} {tool_input}")

    def test_write_tool_outside_scratchpad_root(self):
        # 실제 실수: 사용자 이름까지만 있는 잘못된 scratchpad 경로에 파일을 만들었다.
        self.assert_denied("Write", {"file_path": "/private/tmp/claude-501/-Users-ny/-placeholder-not-used"})

    def test_write_tool_to_plain_tmp(self):
        self.assert_denied("Write", {"file_path": "/tmp/ssf-init-check.py"})

    def test_grok_write_tool_uses_path_key(self):
        self.assert_denied("search_replace", {"path": "/tmp/ssf-init-check.py"}, grok_style=True)

    def test_shell_redirect_to_tmp(self):
        # 실제 실수: worker 가 보고 본문을 /tmp 에 남겼다.
        self.assert_denied("Bash", {"command": "printf '%s' \"$body\" > /tmp/bundle1b-body.txt"})

    def test_grok_shell_append_to_tmp(self):
        self.assert_denied("run_terminal_command", {"command": "echo x >> /private/tmp/report.md"}, grok_style=True)

    def test_shell_write_commands(self):
        for command in (
            "tee /tmp/out.log",
            "touch /tmp/marker",
            "mkdir -p /tmp/work",
            "cp scratch.txt /tmp/copy.txt",
            "curl -o /tmp/page.html https://example.com",
            "dd if=/dev/zero of=/tmp/blob bs=1 count=1",
            "cd x && python3 -c \"open('/tmp/x.json', 'w').write('{}')\"",
        ):
            with self.subTest(command=command):
                self.assert_denied("Bash", {"command": command})

    def test_heredoc_written_to_tmp_is_still_blocked(self):
        command = "cat > /tmp/notes.md <<'EOF'\nhello\nEOF"
        self.assert_denied("Bash", {"command": command})

    def test_lookalike_prefix_is_not_the_scratchpad(self):
        self.assert_denied("Write", {"file_path": "/private/tmp/claude-501/-Users-ny-Documents-programs-resell-sniper-evil/x"})


class AllowedTests(unittest.TestCase):
    def assert_allowed(self, tool_name: str, tool_input: dict, grok_style: bool = False) -> None:
        reply = run_hook(tool_name, tool_input, grok_style)
        self.assertFalse(is_denied(reply), f"통과해야 함: {tool_name} {tool_input} -> {reply}")

    def test_scratchpad_writes(self):
        self.assert_allowed("Write", {"file_path": f"{SCRATCH}/spec.md"})
        self.assert_allowed("Write", {"file_path": SCRATCH.replace("/private", "") + "/spec.md"})
        self.assert_allowed("Bash", {"command": f"echo ok > {SCRATCH}/out.txt"})
        self.assert_allowed("search_replace", {"path": f"{SCRATCH}/a.py"}, grok_style=True)

    def test_heredoc_body_mentioning_tmp_is_data(self):
        # 실제 오탐: 실험 지시서를 scratchpad 에 heredoc 으로 쓰는데 본문의 예시 명령을 쓰기로 오인했다.
        command = (
            f"cat > {SCRATCH}/spec.md <<EOF\n"
            "1. 셸로 `echo probe > /tmp/guard-probe-shell.txt` 실행.\n"
            "EOF\n"
            "echo done"
        )
        self.assert_allowed("Bash", {"command": command})

    def test_scratchpad_root_itself_without_trailing_slash(self):
        root = "/private/tmp/claude-501/-Users-ny-Documents-programs-resell-sniper"
        self.assert_allowed("Bash", {"command": f"mkdir -p {root}"})

    def test_reading_and_cleanup_in_tmp(self):
        for command in (
            "ls /tmp",
            "cat /tmp/ssf-init-check.py | head -3",
            "rm -f /tmp/ssf-init-check.py /tmp/bundle1-naver-url-report.md",
            "ls /tmp | grep -i bundle | wc -l",
            f"cp /tmp/old.log {SCRATCH}/old.log",
        ):
            with self.subTest(command=command):
                self.assert_allowed("Bash", {"command": command})

    def test_project_files_and_other_tools(self):
        self.assert_allowed("Write", {"file_path": "/Users/ny/Documents/programs/resell-sniper/notes.md"})
        self.assert_allowed("Bash", {"command": "git status --short"})
        self.assert_allowed("Read", {"file_path": "/tmp/anything"})

    def test_malformed_input_fails_open(self):
        result = subprocess.run(
            [sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, timeout=HOOK_TIMEOUT_SEC
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")


class SystemTempTests(unittest.TestCase):
    """macOS tempfile 기본 경로(/var/folders)와 $TMPDIR 쓰기."""

    @classmethod
    def setUpClass(cls):
        cls.hook = load_hook()

    def test_write_tool_to_var_folders_denied(self):
        for path in ("/var/folders/xx/T/a.json", "/private/var/folders/xx/T/a.json"):
            with self.subTest(path=path):
                reply = run_hook("Write", {"file_path": path})
                self.assertTrue(is_denied(reply), path)

    def test_shell_redirect_to_var_folders_denied(self):
        reply = run_hook("Bash", {"command": "echo x > /var/folders/xx/T/a"})
        self.assertTrue(is_denied(reply), reply)

    def test_read_and_rm_in_var_folders_allowed(self):
        for command in ("cat /var/folders/xx/T/a", "rm /var/folders/xx/T/a"):
            with self.subTest(command=command):
                reply = run_hook("Bash", {"command": command})
                self.assertFalse(is_denied(reply), f"{command} -> {reply}")

    def test_claude_tmpdir_write_allowed_when_hook_environ_is_system(self):
        # SessionStart 가 Bash TMPDIR 을 루트로 둔다. 훅 프로세스 TMPDIR 은 /var/folders 여도 허용한다.
        env = os.environ.copy()
        env["TMPDIR"] = "/var/folders/xx/T/"
        for command in ("echo x > $TMPDIR/a", "touch ${TMPDIR}/a"):
            with self.subTest(command=command):
                reply = run_hook("Bash", {"command": command}, env=env)
                self.assertFalse(is_denied(reply), f"통과해야 함: {command} -> {reply}")

    def test_grok_tmpdir_write_follows_hook_environ(self):
        commands = ("echo x > $TMPDIR/a", "touch ${TMPDIR}/a")
        denied_env = os.environ.copy()
        denied_env["TMPDIR"] = "/var/folders/xx/T/"
        allowed_env = os.environ.copy()
        allowed_env["TMPDIR"] = self.hook.ALLOWED_ROOTS[0]
        for command in commands:
            with self.subTest(command=command, decision="deny"):
                reply = run_hook("Bash", {"command": command}, grok_style=True, env=denied_env)
                self.assertTrue(is_denied(reply), f"차단되어야 함: {command} -> {reply}")
            with self.subTest(command=command, decision="allow"):
                reply = run_hook("Bash", {"command": command}, grok_style=True, env=allowed_env)
                self.assertFalse(is_denied(reply), f"통과해야 함: {command} -> {reply}")

    def test_tmpdir_write_allowed_takes_caller_and_environ(self):
        system = {"TMPDIR": "/var/folders/xx/T/"}
        root = {"TMPDIR": self.hook.ALLOWED_ROOTS[0]}
        self.assertTrue(self.hook.tmpdir_write_allowed(self.hook.CALLER_CLAUDE, system))
        self.assertFalse(self.hook.tmpdir_write_allowed(self.hook.CALLER_GROK, system))
        self.assertTrue(self.hook.tmpdir_write_allowed(self.hook.CALLER_GROK, root))
        self.assertEqual(
            self.hook.caller_kind({self.hook.CLAUDE_TOOL_NAME_KEY: "Bash", self.hook.CLAUDE_TOOL_INPUT_KEY: {}}),
            self.hook.CALLER_CLAUDE,
        )
        self.assertEqual(
            self.hook.caller_kind({self.hook.GROK_TOOL_NAME_KEY: "Bash", self.hook.GROK_TOOL_INPUT_KEY: {}}),
            self.hook.CALLER_GROK,
        )
        self.assertEqual(self.hook.caller_kind({self.hook.CLAUDE_TOOL_NAME_KEY: None}), self.hook.CALLER_CLAUDE)
        self.assertEqual(
            self.hook.caller_kind({
                self.hook.CLAUDE_TOOL_NAME_KEY: None,
                self.hook.GROK_TOOL_INPUT_KEY: None,
            }),
            self.hook.CALLER_GROK,
        )
        self.assertEqual(self.hook.caller_kind({}), self.hook.CALLER_GROK)

    def test_mixed_payload_literal_tmp_write_denied(self):
        # 키를 호출자별로만 읽으면 반대편 키의 /tmp 쓰기를 놓친다.
        payloads = (
            {"tool_name": "Bash", "toolInput": {"command": "echo x > /tmp/a"}},
            {"toolName": "Bash", "tool_input": {"command": "echo x > /tmp/a"}},
            {"tool_name": "Write", "toolInput": {"file_path": "/tmp/a"}},
            {"toolName": "Write", "tool_input": {"file_path": "/tmp/a"}},
        )
        for payload in payloads:
            with self.subTest(payload=sorted(payload)):
                reply = run_hook_payload(payload)
                self.assertTrue(is_denied(reply), payload)

    def test_mixed_keys_tmpdir_write_uses_grok_rule(self):
        # 두 형식 키가 같이 있으면 Claude 로 보지 않는다. 훅 TMPDIR 이 /var/folders 면 차단.
        command = "echo x > $TMPDIR/a"
        env = os.environ.copy()
        env["TMPDIR"] = "/var/folders/xx/T/"
        payloads = (
            {"tool_name": "Bash", "toolInput": {"command": command}},
            {"toolName": "Bash", "tool_input": {"command": command}},
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "toolName": "Bash",
                "toolInput": {"command": command},
            },
        )
        for payload in payloads:
            with self.subTest(payload=sorted(payload)):
                reply = run_hook_payload(payload, env=env)
                self.assertTrue(is_denied(reply), payload)

    def test_hook_scripts_are_executable(self):
        hooks = HOOK.parent
        for name in ("block-tmp-writes.py", "session-tmpdir.py"):
            path = hooks / name
            with self.subTest(path=path.name):
                self.assertTrue(os.access(path, os.X_OK), path)

    def test_tmpdir_old_name_is_allowed_when_hook_tmpdir_is_system(self):
        # $TMPDIR_OLD 는 $TMPDIR 이 아니다. 부정 전방탐색이 없으면 접두로 오인한다.
        env = os.environ.copy()
        env["TMPDIR"] = "/var/folders/xx/T/"
        for grok_style in (False, True):
            for command in ("echo x > $TMPDIR_OLD/x", "touch $TMPDIR_OLD/x"):
                with self.subTest(command=command, grok_style=grok_style):
                    reply = run_hook("Bash", {"command": command}, grok_style=grok_style, env=env)
                    self.assertFalse(is_denied(reply), f"통과해야 함: {command} -> {reply}")

    def test_settings_session_start_registers_tmpdir_hook(self):
        settings_path = HOOK.parent.parent / "settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertNotIn("env", settings)
        self.assertIn("PreToolUse", settings["hooks"])
        self.assertIn("PostToolUse", settings["hooks"])
        groups = settings["hooks"]["SessionStart"]
        commands = [hook["command"] for group in groups for hook in group["hooks"]]
        self.assertTrue(any("session-tmpdir.py" in command for command in commands), commands)
        self.assertTrue(all("matcher" not in group for group in groups))


class QuoteAndExpansionTests(unittest.TestCase):
    """따옴표로 감싼 쓰기 대상과 ${TMPDIR:-기본값} 매개변수 확장."""

    @classmethod
    def setUpClass(cls):
        cls.hook = load_hook()
        cls.head = load_module(HEAD_HOOK, "block_tmp_writes_head")
        cls.system_env = os.environ.copy()
        cls.system_env["TMPDIR"] = "/var/folders/xx/T/"
        cls.root_env = os.environ.copy()
        cls.root_env["TMPDIR"] = cls.hook.ALLOWED_ROOTS[0]
        cls.allowed_root = cls.hook.ALLOWED_ROOTS[0].rstrip("/")

    def assert_denied(self, command: str, *, grok_style: bool, env: dict) -> None:
        reply = run_hook("Bash", {"command": command}, grok_style=grok_style, env=env)
        self.assertTrue(is_denied(reply), f"차단되어야 함: {command} grok={grok_style} -> {reply}")

    def assert_allowed(self, command: str, *, grok_style: bool, env: dict) -> None:
        reply = run_hook("Bash", {"command": command}, grok_style=grok_style, env=env)
        self.assertFalse(is_denied(reply), f"통과해야 함: {command} grok={grok_style} -> {reply}")

    def test_quoted_literal_redirects_denied(self):
        commands = (
            'echo x > "/tmp/a"',
            "echo x > '/tmp/a'",
            'echo x > "/var/folders/xx/a"',
            "echo x > '/var/folders/xx/a'",
            'echo x >> "/tmp/a"',
            'echo x >"/tmp/a"',
            "echo x >>'/var/folders/xx/a'",
        )
        for command in commands:
            for grok_style in (False, True):
                with self.subTest(command=command, grok_style=grok_style):
                    self.assert_denied(command, grok_style=grok_style, env=self.system_env)

    def test_quoted_output_flags_denied(self):
        commands = (
            'curl -o "/tmp/page.html" https://example.com',
            "curl --output '/tmp/page.html' https://example.com",
            'curl -o="/tmp/page.html" https://example.com',
            'dd if=/dev/zero of="/tmp/blob" bs=1 count=1',
            "dd if=/dev/zero of='/var/folders/xx/blob' bs=1 count=1",
        )
        for command in commands:
            for grok_style in (False, True):
                with self.subTest(command=command, grok_style=grok_style):
                    self.assert_denied(command, grok_style=grok_style, env=self.system_env)

    def test_quoted_tmpdir_ref_follows_caller(self):
        # 따옴표가 있어도 $TMPDIR 판정은 호출자별로 같다. Claude 는 허용, Grok 은 훅 TMPDIR 기준.
        commands = (
            'echo x > "$TMPDIR/x"',
            'echo x > "${TMPDIR}/x"',
            "echo x > '$TMPDIR/x'",
        )
        for command in commands:
            with self.subTest(command=command, who="claude"):
                self.assert_allowed(command, grok_style=False, env=self.system_env)
            with self.subTest(command=command, who="grok-system"):
                self.assert_denied(command, grok_style=True, env=self.system_env)
            with self.subTest(command=command, who="grok-root"):
                self.assert_allowed(command, grok_style=True, env=self.root_env)

    def test_expansion_temp_default_denied_for_every_caller(self):
        commands = (
            "echo x > ${TMPDIR:-/tmp}/x",
            'echo x > "${TMPDIR:-/tmp}/x"',
            "echo x > ${TMPDIR:=/tmp}/x",
            "echo x > ${TMPDIR-/tmp}/x",
            "echo x > ${TMPDIR=/var/folders/xx/a}",
            "touch ${TMPDIR:-/tmp}/x",
        )
        envs = (("system", self.system_env), ("root", self.root_env))
        for command in commands:
            for grok_style in (False, True):
                for name, env in envs:
                    with self.subTest(command=command, grok_style=grok_style, env=name):
                        self.assert_denied(command, grok_style=grok_style, env=env)

    def test_expansion_allowed_default_follows_caller(self):
        command = f"echo x > ${{TMPDIR:-{self.allowed_root}/x}}"
        self.assert_allowed(command, grok_style=False, env=self.system_env)
        self.assert_allowed(command, grok_style=False, env=self.root_env)
        self.assert_denied(command, grok_style=True, env=self.system_env)
        self.assert_allowed(command, grok_style=True, env=self.root_env)
        # /tmpfoo 는 /tmp 가 아니다. Claude 는 통과하고 Grok 은 호출자 규칙만 적용한다.
        lookalike = "echo x > ${TMPDIR:-/tmpfoo}/y"
        self.assert_allowed(lookalike, grok_style=False, env=self.system_env)
        self.assert_denied(lookalike, grok_style=True, env=self.system_env)
        self.assert_allowed(lookalike, grok_style=True, env=self.root_env)

    def test_quoted_write_commands_denied(self):
        # _command_words 가 따옴표를 벗기므로 tee/touch/mkdir/cp 도 따옴표 경로를 쓴다.
        commands = (
            'tee "/tmp/out.log"',
            "touch '/tmp/marker'",
            'mkdir -p "/tmp/work"',
            'cp scratch.txt "/tmp/copy.txt"',
            f'cp "{SCRATCH}/a.txt" "/tmp/b.txt"',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assert_denied(command, grok_style=True, env=self.system_env)

    def test_quoted_literals_and_reads_allowed(self):
        commands = (
            'echo "see /tmp/a"',
            'cat "/tmp/a"',
            "cat '/tmp/a'",
            'rm "/tmp/a"',
            "rm '/tmp/a'",
            f'cp "/tmp/old.log" "{SCRATCH}/old.log"',
            "echo x > $TMPDIR_OLD/x",
            'echo x > "$TMPDIR_OLD/x"',
        )
        for command in commands:
            for grok_style in (False, True):
                with self.subTest(command=command, grok_style=grok_style):
                    self.assert_allowed(command, grok_style=grok_style, env=self.system_env)

    def test_head_false_positives_still_denied(self):
        # HEAD 가 문자열 안의 > 를 그대로 본다. 이 오탐은 유지한다.
        commands = (
            'grep "> /tmp" file',
            "grep '> /tmp' file",
            'echo "echo > /tmp/x"',
            'bash -c "grep \\"> /tmp\\" file"',
            "bash -c 'grep \"> /tmp\" file'",
            'git commit -m "fix: echo > /tmp/x"',
        )
        for command in commands:
            for grok_style in (False, True):
                with self.subTest(command=command, grok_style=grok_style):
                    self.assert_denied(command, grok_style=grok_style, env=self.system_env)

    def test_wrapped_shell_writes_denied(self):
        # HEAD 판정이 감싼 명령 안의 >·-o 를 그대로 본다.
        nested = "echo > /tmp/a"
        for _ in range(SHELL_NEST_DEPTH):
            nested = "bash -c '" + nested + "'"
        commands = (
            'bash -c "echo x > /tmp/a"',
            "sh -c 'echo x > /tmp/a'",
            'eval "echo x > /tmp/a"',
            "timeout 5 bash -c 'curl -o /tmp/a http://e'",
            'timeout -s KILL 5 bash -c "echo > /tmp/a"',
            'sudo -u root bash -c "echo > /tmp/a"',
            'bash -o pipefail -c "echo > /tmp/a"',
            'nice bash -c "echo > /tmp/a"',
            'time bash -c "echo > /tmp/a"',
            'stdbuf -oL bash -c "echo > /tmp/a"',
            'xargs sh -c "echo > /tmp/a"',
            "find . -name x -exec sh -c 'cat {} > /tmp/a' \\;",
            'su -c "echo > /tmp/a"',
            'script -q -c "echo > /tmp/a" /dev/null',
            """python3 -c "import os; os.system('echo > /tmp/a')" """,
            nested,
            'echo \\"x > /tmp/a',
        )
        for command in commands:
            for grok_style in (False, True):
                with self.subTest(command=command, grok_style=grok_style):
                    self.assert_denied(command, grok_style=grok_style, env=self.system_env)

    def test_head_deny_implies_final_deny(self):
        """fixture(577f2f9 고정 복사)가 막으면 현재 훅도 막는다."""
        nested = "echo > /tmp/a"
        for _ in range(SHELL_NEST_DEPTH):
            nested = "bash -c '" + nested + "'"
        commands = [
            "printf '%s' \"$body\" > /tmp/bundle1b-body.txt",
            "echo x >> /private/tmp/report.md",
            "tee /tmp/out.log",
            "touch /tmp/marker",
            "mkdir -p /tmp/work",
            "cp scratch.txt /tmp/copy.txt",
            "curl -o /tmp/page.html https://example.com",
            "dd if=/dev/zero of=/tmp/blob bs=1 count=1",
            "cd x && python3 -c \"open('/tmp/x.json', 'w').write('{}')\"",
            "cat > /tmp/notes.md <<'EOF'\nhello\nEOF",
            "ls /tmp",
            "cat /tmp/ssf-init-check.py | head -3",
            "rm -f /tmp/ssf-init-check.py",
            f"cp /tmp/old.log {SCRATCH}/old.log",
            f"echo ok > {SCRATCH}/out.txt",
            'echo x > "/tmp/a"',
            "echo x > '/tmp/a'",
            'echo x > "$TMPDIR/x"',
            "echo x > ${TMPDIR:-/tmp}/x",
            'grep "> /tmp" file',
            "grep '> /tmp' file",
            'echo "see /tmp/a"',
            'echo "echo > /tmp/x"',
            'cat "/tmp/a"',
            "cat '/tmp/a'",
            'rm "/tmp/a"',
            'tee "/tmp/out.log"',
            "touch '/tmp/marker'",
            'cp scratch.txt "/tmp/copy.txt"',
            'bash -c "echo x > /tmp/a"',
            "sh -c 'echo x > /tmp/a'",
            'eval "echo x > /tmp/a"',
            "timeout 5 bash -c 'curl -o /tmp/a http://e'",
            'timeout -s KILL 5 bash -c "echo > /tmp/a"',
            'sudo -u root bash -c "echo > /tmp/a"',
            'bash -o pipefail -c "echo > /tmp/a"',
            'nice bash -c "echo > /tmp/a"',
            'time bash -c "echo > /tmp/a"',
            'stdbuf -oL bash -c "echo > /tmp/a"',
            'xargs sh -c "echo > /tmp/a"',
            "find . -name x -exec sh -c 'cat {} > /tmp/a' \\;",
            'su -c "echo > /tmp/a"',
            'script -q -c "echo > /tmp/a" /dev/null',
            """python3 -c "import os; os.system('echo > /tmp/a')" """,
            nested,
            'echo \\"x > /tmp/a',
            'grep -o "/tmp" file',
            'echo of="/tmp/a"',
        ]
        callers = (
            (self.hook.CALLER_CLAUDE, self.system_env),
            (self.hook.CALLER_GROK, self.system_env),
            (self.hook.CALLER_GROK, self.root_env),
        )
        for command in commands:
            for caller, env in callers:
                head = self.head._blocked_command_path(command, env, caller)
                if head is None:
                    continue
                final = self.hook._blocked_command_path(command, env, caller)
                with self.subTest(command=command, caller=caller):
                    self.assertIsNotNone(final, f"HEAD 가 막음({head})인데 최종은 통과: {command}")

    def test_unbalanced_quote_repeat_finishes_within_limit(self):
        command = '"' + "x > /tmp/a " * UNBALANCED_QUOTE_REPEAT
        started = time.monotonic()
        self.assert_denied(command, grok_style=True, env=self.system_env)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, HOOK_PERF_MAX_SEC, f"{elapsed:.3f}s")

    def test_many_operators_finish_within_limit(self):
        parts = " ".join(
            f"if a > 1: p('/tmp/f{index % 3}')" for index in range(REDOS_OPERATOR_REPEAT)
        )
        command = 'python3 -c "' + parts + '"'
        started = time.monotonic()
        self.assert_allowed(command, grok_style=True, env=self.system_env)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, HOOK_PERF_MAX_SEC, f"{elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
