from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


PYTHON = sys.executable
CPP_STD = "c++20"
COMPAT_INCLUDE_DIR = Path(__file__).resolve().parent / "compat"
GEN_FUNC_NAMES = ("generate", "data_make", "dataMake", "make_data", "makeData")


###############################################################################
# VS Code 실행 버튼용 설정
#
# 터미널에 명령어를 쓰지 않고 VS Code의 "Run Python File" 버튼으로 실행하려면
# 아래 값만 수정한 뒤 이 파일을 실행하면 됩니다.
#
# 경로는 이 파일(hybrid_counter_maker.py)이 있는 폴더 기준 상대 경로 또는 절대 경로를
# 모두 사용할 수 있습니다. 예: "../work/answer.py", "C:/Users/.../answer.cpp"
###############################################################################
RUN_BY_VSCODE_BUTTON = True

BUTTON_CONFIG = {
    # "compare": 정답/오답 코드를 비교해 반례 찾기
    # "generate": 정답 코드로 입력/출력 데이터 만들기
    "mode": "compare",

    # 공통 설정
    "generator": "./work/generator.py",
    "generator_mode": "auto",  # "auto", "function", "script"
    "answer": "./work/_answer.cpp",
    # "auto"이면 PATH의 g++ 또는 GenCounter2에 포함된 MinGW를 자동으로 찾습니다.
    "compiler": "C:/Program Files/CodeBlocks/MinGW/bin/g++.exe",
    "timeout": 5.0,

    # compare 모드 설정
    "test": "./work/_wrong.cpp",
    "loop": 10000,
    "start": 1,
    "need": 1,
    "params": {},              # 예: {"N": 100}
    "special_judge": "",       # 예: "special_judge.cpp", 없으면 빈 문자열
    "counter_dir": "CounterData",
    "judge_work_dir": ".judge_tmp",
    "preview": 10,             # 비교 중 보여줄 입력 앞 글자 수

    # generate 모드 설정
    "out_dir": "DATA",
    "count": 10,
    # plan을 쓰면 count/start/params 대신 아래 케이스 목록대로 생성합니다.
    # 예: [{"no": 1, "N": 1}, {"no": 2, "N": 10}, {"no": 3, "N": 100}]
    "plan": [],
}


class HybridError(Exception):
    pass


@dataclass
class RunResult:
    output: str
    returncode: int


@dataclass
class CaseSpec:
    no: int
    params: dict[str, Any]


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def comparable(text: str) -> str:
    return normalize_newlines(text).strip()


def preview(text: str, size: int) -> str:
    snippet = normalize_newlines(text)[:size]
    return snippet.replace("\n", "\\n")


def file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_hashes(cache_path: Path) -> dict[str, str]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_hashes(cache_path: Path, hashes: dict[str, str]) -> None:
    cache_path.write_text(json.dumps(hashes, indent=2), encoding="utf-8")


def compiler_output(args: list[str], compiler: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input="int main() { return 0; }\n",
        text=True,
        capture_output=True,
        env=run_env_with_compiler(compiler),
    )


def resolve_compiler(compiler: str) -> str:
    candidates: list[Path | str] = []
    if compiler and compiler != "auto":
        candidates.append(compiler)
    else:
        which = shutil.which("g++")
        if which:
            candidates.append(which)
        base = Path(__file__).resolve().parent
        candidates.extend(
            [
                Path("C:/Program Files/CodeBlocks/MinGW/bin/g++.exe"),
                base / "MinGW" / "bin" / "g++.exe",
                base.parent / "MinGW" / "bin" / "g++.exe",
                base.parent / "work" / "GenCounter2" / "MinGW" / "bin" / "g++.exe",
                Path.cwd() / "MinGW" / "bin" / "g++.exe",
                Path.cwd() / "work" / "GenCounter2" / "MinGW" / "bin" / "g++.exe",
            ]
        )

    for candidate in candidates:
        candidate_text = str(candidate)
        if Path(candidate_text).exists():
            return candidate_text
        found = shutil.which(candidate_text)
        if found:
            return found

    raise HybridError(
        "C++ compiler not found. Set BUTTON_CONFIG['compiler'] to g++.exe path "
        'or place MinGW at "MinGW/bin/g++.exe".'
    )


def run_env_with_compiler(compiler: str) -> dict[str, str]:
    env = os.environ.copy()
    try:
        compiler_path = Path(resolve_compiler(compiler))
    except HybridError:
        return env
    if compiler_path.exists():
        compiler_dir = str(compiler_path.parent)
        env["PATH"] = compiler_dir + os.pathsep + env.get("PATH", "")
    return env


def compiler_version_text(compiler_path: str, compiler: str) -> str:
    try:
        proc = subprocess.run(
            [compiler_path, "-v"],
            text=True,
            capture_output=True,
            env=run_env_with_compiler(compiler),
        )
    except FileNotFoundError:
        return ""
    return proc.stdout + proc.stderr


def resolve_cpp_std_flag(compiler_path: str, compiler: str) -> str:
    candidates = [CPP_STD]
    if CPP_STD == "c++20":
        candidates.append("c++2a")

    for standard in candidates:
        flag = f"-std={standard}"
        proc = compiler_output(
            [compiler_path, flag, "-x", "c++", "-", "-fsyntax-only"],
            compiler,
        )
        if proc.returncode == 0:
            return flag

    tried = ", ".join(f"-std={standard}" for standard in candidates)
    raise HybridError(f"C++ compiler does not support requested standard ({tried}).")


def cpp_include_args(compiler_path: str, compiler: str, std_flag: str) -> list[str]:
    version = compiler_version_text(compiler_path, compiler)
    needs_bits_compat = (
        COMPAT_INCLUDE_DIR.exists()
        and std_flag in ("-std=c++17", "-std=c++20", "-std=c++2a")
        and "gcc version 8.1.0" in version
        and "mingw" in version.lower()
    )
    if needs_bits_compat:
        return ["-I", str(COMPAT_INCLUDE_DIR)]
    return []


def compile_cpp(path: Path, compiler: str, force: bool = False) -> Path:
    exe = path.with_suffix(".exe" if os.name == "nt" else "")
    cache_path = path.with_name(".hybrid_counter_hashes.json")
    hashes = load_hashes(cache_path)
    compiler_path = resolve_compiler(compiler)
    std_flag = resolve_cpp_std_flag(compiler_path, compiler)
    include_args = cpp_include_args(compiler_path, compiler, std_flag)
    key = "|".join([str(path.resolve()), compiler_path, std_flag, *include_args])
    now = file_hash(path)

    if not force and exe.exists() and hashes.get(key) == now:
        return exe

    cmd = [
        compiler_path,
        "-O2",
        "-Wall",
        std_flag,
        *include_args,
        str(path),
        "-o",
        str(exe),
    ]
    print(f"[build] {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, env=run_env_with_compiler(compiler))
    except FileNotFoundError as exc:
        raise HybridError(f"C++ compiler execution failed: {exc}") from exc
    if proc.returncode != 0:
        raise HybridError(proc.stderr.strip() or f"C++ build failed: {path}")

    hashes[key] = now
    save_hashes(cache_path, hashes)
    return exe


def command_for_code(path: Path, compiler: str) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return [PYTHON, str(path)]
    if suffix in (".cpp", ".cc", ".cxx"):
        return [str(compile_cpp(path, compiler))]
    if suffix in (".exe", ""):
        return [str(path)]
    raise HybridError(f"Unsupported code file type: {path}")


def run_code(path: Path, input_text: str, args: list[str], compiler: str, timeout: float) -> RunResult:
    cmd = command_for_code(path, compiler) + args
    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=run_env_with_compiler(compiler),
        )
    except FileNotFoundError as exc:
        raise HybridError(f"Code execution failed: {cmd[0]} ({exc})") from exc
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return RunResult(output=comparable(proc.stdout), returncode=proc.returncode)


def has_generator_function(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except SyntaxError:
        return False
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return any(name in names for name in GEN_FUNC_NAMES)


class Generator:
    def __init__(self, path: Path, mode: str) -> None:
        self.path = path
        self.mode = mode
        self.func: Callable[..., Any] | None = None

        if mode == "auto":
            self.mode = "function" if path.suffix == ".py" and has_generator_function(path) else "script"

        if self.mode == "function":
            self.func = self._load_function()

    def _load_function(self) -> Callable[..., Any]:
        spec = importlib.util.spec_from_file_location("hybrid_user_generator", self.path)
        if spec is None or spec.loader is None:
            raise HybridError(f"Cannot load generator: {self.path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for name in GEN_FUNC_NAMES:
            func = getattr(module, name, None)
            if callable(func):
                return func
        raise HybridError(
            f"Function generator must define one of: {', '.join(GEN_FUNC_NAMES)}"
        )

    def make(self, case: CaseSpec, timeout: float) -> str:
        if self.mode == "script":
            return self._make_by_script(case, timeout)
        return self._make_by_function(case)

    def _make_by_script(self, case: CaseSpec, timeout: float) -> str:
        payload = json.dumps(case.params, ensure_ascii=False)
        proc = subprocess.run(
            [PYTHON, str(self.path), str(case.no), payload],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise HybridError(proc.stderr.strip() or f"Generator failed on case {case.no}")
        return normalize_newlines(proc.stdout).rstrip("\n")

    def _make_by_function(self, case: CaseSpec) -> str:
        assert self.func is not None
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                value = self.func(case.no, case.params)
            except TypeError:
                try:
                    value = self.func(case.no)
                except TypeError:
                    value = self.func()
        printed = buf.getvalue()
        if value is None:
            text = printed
        elif isinstance(value, str):
            text = printed + value
        else:
            text = printed + str(value)
        return normalize_newlines(text).rstrip("\n")


def parse_plan_value(raw: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    if raw.startswith("@"):
        return json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        params: dict[str, Any] = {}
        for part in raw.split(","):
            if not part:
                continue
            if "=" not in part:
                raise HybridError(f"Bad params item: {part}")
            key, value = part.split("=", 1)
            try:
                params[key] = json.loads(value)
            except json.JSONDecodeError:
                params[key] = value
        return params


def load_cases(args: argparse.Namespace) -> list[CaseSpec]:
    plan_cases = getattr(args, "plan_cases", None)
    if plan_cases:
        raw = plan_cases
        cases = []
        for index, item in enumerate(raw, start=1):
            if isinstance(item, int):
                cases.append(CaseSpec(no=item, params={}))
            elif isinstance(item, dict):
                no = int(item.get("no", item.get("case", index)))
                params = {k: v for k, v in item.items() if k not in ("no", "case")}
                cases.append(CaseSpec(no=no, params=params))
            else:
                raise HybridError(f"Unsupported plan item: {item!r}")
        return cases

    if args.plan:
        raw = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "cases" in raw:
            raw = raw["cases"]
        cases = []
        for index, item in enumerate(raw, start=1):
            if isinstance(item, int):
                cases.append(CaseSpec(no=item, params={}))
            elif isinstance(item, dict):
                no = int(item.get("no", item.get("case", index)))
                params = {k: v for k, v in item.items() if k not in ("no", "case")}
                cases.append(CaseSpec(no=no, params=params))
            else:
                raise HybridError(f"Unsupported plan item: {item!r}")
        return cases

    params = parse_plan_value(args.params or "")
    return [CaseSpec(no=i, params=dict(params)) for i in range(args.start, args.start + args.count)]


def resolve_config_path(value: str | Path | None) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((Path(__file__).resolve().parent / path).resolve())


def button_namespace(config: dict[str, Any]) -> SimpleNamespace:
    mode = config.get("mode", "compare")
    if mode not in ("compare", "generate"):
        raise HybridError('BUTTON_CONFIG["mode"] must be "compare" or "generate"')

    common = {
        "command": mode,
        "compiler": config.get("compiler", "g++"),
        "timeout": float(config.get("timeout", 5.0)),
        "generator": resolve_config_path(config.get("generator", "")),
        "generator_mode": config.get("generator_mode", "auto"),
        "answer": resolve_config_path(config.get("answer", "")),
        "params": config.get("params", {}),
        "start": int(config.get("start", 1)),
        "count": int(config.get("count", 10)),
        "plan": "",
        "plan_cases": config.get("plan", []),
    }

    if mode == "generate":
        return SimpleNamespace(
            **common,
            out_dir=resolve_config_path(config.get("out_dir", "DATA")),
            func=generate_data,
        )

    return SimpleNamespace(
        **common,
        test=resolve_config_path(config.get("test", "")),
        loop=int(config.get("loop", 10000)),
        need=int(config.get("need", 1)),
        special_judge=resolve_config_path(config.get("special_judge", "")),
        counter_dir=resolve_config_path(config.get("counter_dir", "CounterData")),
        judge_work_dir=resolve_config_path(config.get("judge_work_dir", ".judge_tmp")),
        preview=int(config.get("preview", 10)),
        func=compare,
    )


def write_case(out_dir: Path, case_no: int, input_text: str, answer_text: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"d{case_no}.in").write_text(input_text + "\n", encoding="utf-8", newline="\n")
    (out_dir / f"d{case_no}.out").write_text(answer_text + "\n", encoding="utf-8", newline="\n")


def generate_data(args: argparse.Namespace) -> None:
    generator = Generator(Path(args.generator), args.generator_mode)
    answer = Path(args.answer)
    cases = load_cases(args)
    out_dir = Path(args.out_dir)

    for case in cases:
        print(f"[generate] case {case.no}")
        input_text = generator.make(case, args.timeout)
        result = run_code(answer, input_text, [str(case.no)], args.compiler, args.timeout)
        if result.returncode != 0:
            raise HybridError(f"Answer code failed on case {case.no}")
        write_case(out_dir, case.no, input_text, result.output)
        print(f"[saved] {out_dir / f'd{case.no}.in'} / {out_dir / f'd{case.no}.out'}")


def run_special_judge(
    judge: Path,
    input_text: str,
    answer_text: str,
    test_text: str,
    compiler: str,
    timeout: float,
    work_dir: Path,
) -> int:
    work_dir.mkdir(parents=True, exist_ok=True)
    fin = work_dir / "fin.txt"
    fsol = work_dir / "fsol.txt"
    fuser = work_dir / "fuser.txt"
    fin.write_text(input_text, encoding="utf-8")
    fsol.write_text(answer_text, encoding="utf-8")
    fuser.write_text(test_text, encoding="utf-8")
    return run_code(judge, "", [str(fin), str(fsol), str(fuser)], compiler, timeout).returncode


def save_counter(args: argparse.Namespace, counter_no: int, input_text: str, answer_text: str, test_text: str) -> None:
    out_dir = Path(args.counter_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"counter{counter_no}.in").write_text(input_text + "\n", encoding="utf-8", newline="\n")
    (out_dir / f"counter{counter_no}.ans").write_text(answer_text + "\n", encoding="utf-8", newline="\n")
    (out_dir / f"counter{counter_no}.user").write_text(test_text + "\n", encoding="utf-8", newline="\n")


def compare(args: argparse.Namespace) -> None:
    generator = Generator(Path(args.generator), args.generator_mode)
    answer = Path(args.answer)
    test = Path(args.test)
    judge = Path(args.special_judge) if args.special_judge else None
    found = 0

    for i in range(args.start, args.start + args.loop):
        case = CaseSpec(no=i, params=parse_plan_value(args.params or ""))
        input_text = generator.make(case, args.timeout)
        print(f"[case {i}] input first {args.preview} chars: {preview(input_text, args.preview)}")

        answer_res = run_code(answer, input_text, [str(i)], args.compiler, args.timeout)
        test_res = run_code(test, input_text, [str(i)], args.compiler, args.timeout)
        if answer_res.returncode != 0:
            raise HybridError(f"Answer code failed on case {i}")
        if test_res.returncode != 0:
            raise HybridError(f"Test code failed on case {i}")

        if judge:
            ret = run_special_judge(
                judge,
                input_text,
                answer_res.output,
                test_res.output,
                args.compiler,
                args.timeout,
                Path(args.judge_work_dir),
            )
            ok = ret == 0
            if ret not in (0, 1):
                raise HybridError(f"Special judge failed on case {i}, return code {ret}")
        else:
            ok = answer_res.output == test_res.output

        if ok:
            print("[ok]")
            continue

        found += 1
        print("[counterexample]")
        print("==== input ====")
        print(input_text)
        print(f"==== answer: {answer} ====")
        print(answer_res.output)
        print(f"==== test: {test} ====")
        print(test_res.output)
        save_counter(args, found, input_text, answer_res.output, test_res.output)
        print(f"[saved] {Path(args.counter_dir).resolve()}")
        if found >= args.need:
            break

    if found == 0:
        print("[done] no counterexample found")
    else:
        print(f"[done] found {found} counterexample(s)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Flexible test data maker and counterexample finder.")
    parser.add_argument("--compiler", default="g++", help="C++ compiler path. default: g++")
    parser.add_argument("--timeout", type=float, default=5.0, help="Timeout per run in seconds.")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Generate input/output data using the answer code.")
    gen.add_argument("--generator", required=True, help="Random/flexible input generator file.")
    gen.add_argument("--generator-mode", choices=("auto", "script", "function"), default="auto")
    gen.add_argument("--answer", required=True, help="Accepted solution: .py/.cpp/.exe")
    gen.add_argument("--out-dir", default="DATA")
    gen.add_argument("--count", type=int, default=10)
    gen.add_argument("--start", type=int, default=1)
    gen.add_argument("--params", default="", help='JSON, key=value pairs, or @params.json. Example: N=100')
    gen.add_argument("--plan", help='JSON list, e.g. [{"no":1,"N":1},{"no":2,"N":10}]')
    gen.set_defaults(func=generate_data)

    cmp_parser = sub.add_parser("compare", help="Find counterexamples by comparing answer and test code.")
    cmp_parser.add_argument("--generator", required=True)
    cmp_parser.add_argument("--generator-mode", choices=("auto", "script", "function"), default="auto")
    cmp_parser.add_argument("--answer", required=True)
    cmp_parser.add_argument("--test", required=True)
    cmp_parser.add_argument("--loop", type=int, default=10000)
    cmp_parser.add_argument("--start", type=int, default=1)
    cmp_parser.add_argument("--need", type=int, default=1)
    cmp_parser.add_argument("--params", default="")
    cmp_parser.add_argument("--special-judge", help="Special judge: .py/.cpp/.exe. Return 0=AC, 1=WA.")
    cmp_parser.add_argument("--counter-dir", default="CounterData")
    cmp_parser.add_argument("--judge-work-dir", default=".judge_tmp")
    cmp_parser.add_argument("--preview", type=int, default=10, help="Input preview length printed while comparing.")
    cmp_parser.set_defaults(func=compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if RUN_BY_VSCODE_BUTTON and not argv:
        args = button_namespace(BUTTON_CONFIG)
        print(f"[button-config] mode={args.command}")
    else:
        parser = build_parser()
        args = parser.parse_args(argv)

    try:
        args.func(args)
        return 0
    except subprocess.TimeoutExpired as exc:
        print(f"[error] timeout: {exc}", file=sys.stderr)
        return 2
    except HybridError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
