"""Risk tier of a change from what it touches, never from who wrote it."""
import fnmatch
import re

DEFAULT_RULES = {
    "code_globs": ["*.c", "*.cc", "*.cpp", "*.h", "*.hpp", "*.rs", "*.go", "*.py", "*.js", "*.ts", "*.java",
                   "*.cmake", "*CMakeLists.txt", "*.sh", "*.ps1", "*.s", "*.asm"],
    "red_paths": [".github/*", ".gitmodules", "*.cmake", "cmake/*", "*.sh", "*.ps1", "*.bat", "Makefile",
                  "package.json", "*.lock", "requirements*.txt", "Cargo.toml", "go.mod", "third_party/*", "3rdparty/*",
                  "vendor/*"],
    "amber_paths": ["*CMakeLists.txt", "tools/*", "scripts/*"],
    "red_patterns": [
        r"FetchContent_Declare|ExternalProject_Add|file\s*\(\s*DOWNLOAD|execute_process|add_custom_command",
        r"\b(WSAStartup|socket|connect|getaddrinfo|WinHttp\w*|InternetOpen\w*|URLDownloadToFile\w*|curl_\w+)\s*\(",
        r"\b(CreateProcess\w*|ShellExecute\w*|WinExec|system|_?popen|_wpopen|execv\w*|posix_spawn|subprocess\.\w+|os\.system)\s*\(",
        r"\b(WriteProcessMemory|CreateRemoteThread|OpenProcess|SetWindowsHookEx\w*)\s*\(",
        r"\b(Reg(Open|Create|Set|Delete)\w*)\s*\(",
        r"https?://",
        r"[A-Za-z0-9+/]{120,}={0,2}|(0x[0-9a-fA-F]{2},\s*){48,}",
        r"\b(eval|exec)\s*\(",
    ],
    "amber_patterns": [
        r"\b(VirtualProtect|VirtualAlloc\w*|mprotect|mmap|MapViewOfFile\w*)\s*\(|PAGE_EXECUTE",
        r"\b(AddVectoredExceptionHandler|SetUnhandledExceptionFilter|signal|sigaction)\s*\(",
        r"\b(LoadLibrary\w*|GetProcAddress|dlopen|dlsym)\s*\(",
        r"\b(CreateFile\w*|DeleteFile\w*|MoveFile\w*|fopen|_wfopen|open|unlink|remove)\s*\(",
        r"\bgetenv\s*\(|os\.environ",
        r"\b(memcpy|strcpy|sprintf|alloca)\s*\(",
    ],
}

RANK = {"green": 0, "amber": 1, "red": 2}


def matches(path, globs):
    return any(fnmatch.fnmatch(path, g) for g in globs)


def findings(paths, added, rules=None):
    """paths: changed file paths. added: (path, line_number, text) for every added line."""
    rules = rules or DEFAULT_RULES
    out = []
    for p in paths:
        if matches(p, rules["red_paths"]):
            out.append(("red", p, None, "sensitive path"))
        elif matches(p, rules["amber_paths"]):
            out.append(("amber", p, None, "build or tooling path"))
    for path, line, text in added:
        if not matches(path, rules["code_globs"]):
            continue
        for level in ("red", "amber"):
            for pattern in rules[f"{level}_patterns"]:
                if re.search(pattern, text):
                    out.append((level, path, line, pattern))
    return out


def classify(paths, added, rules=None):
    levels = [f[0] for f in findings(paths, added, rules)]
    return max(levels, key=RANK.get) if levels else "green"


def parse_diff(diff):
    """Unified diff -> (paths, added lines)."""
    paths, added, path, line = [], [], None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else None
            if path:
                paths.append(path)
        elif raw.startswith("--- a/"):
            if raw[6:] not in paths:
                paths.append(raw[6:])
        elif raw.startswith("@@"):
            found = re.search(r"\+(\d+)", raw)
            line = int(found.group(1)) if found else 0
        elif raw.startswith("+") and path:
            added.append((path, line, raw[1:]))
            line += 1
        elif not raw.startswith("-"):
            line += 1
    return paths, added
