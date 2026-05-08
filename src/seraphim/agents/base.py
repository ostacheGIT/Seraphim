"""Agents built-in : chat, coder, researcher, react."""

import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
from seraphim.agents.core import AgentContext, BaseAgent
from seraphim.skills.registry import discover_skills, get_all_tools, get_skill, SKILL_REGISTRY

# State set by CoderAgent — used for execution confirmation
_pending_code: str | None = None
_pending_file: str | None = None

_MAX_OUTPUT = 4000


def _extract_args_json(response: str) -> dict:
    """Extrait le JSON après ARGS: en comptant les accolades pour gérer les {} imbriqués."""
    m = re.search(r"ARGS:\s*(\{)", response)
    if not m:
        return {}
    start = m.start(1)
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(response[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                raw = response[start : i + 1].replace("\\\\", "\\")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return {}
    return {}


def _format_install_cmd(step: dict, is_windows: bool) -> str:
    kind = step.get("kind", "")
    formula = step.get("formula") or step.get("package") or step.get("id", "")
    label = step.get("label", "")
    if kind == "winget":
        return f"`winget install {formula}` — {label}" if label else f"`winget install {formula}`"
    if kind == "choco":
        return f"`choco install {formula}` — {label}" if label else f"`choco install {formula}`"
    if kind == "scoop":
        return f"`scoop install {formula}` — {label}" if label else f"`scoop install {formula}`"
    if kind == "brew":
        cmd = f"`brew install {formula}`"
        if is_windows:
            cmd = f"`winget install --id {formula}` (ou brew si WSL)"
        return f"{cmd} — {label}" if label else cmd
    if kind in ("apt", "apt-get"):
        return f"`sudo apt install {formula}` — {label}" if label else f"`sudo apt install {formula}`"
    return label or formula


async def _run_code(code: str, file_path: str | None) -> str:
    """Run generated code. Prefer executing the saved file; fall back to code_interpreter."""
    import sys as _sys
    if file_path and Path(file_path).exists():
        try:
            proc = subprocess.run(
                [_sys.executable, file_path],
                capture_output=True, text=True, timeout=30,
            )
            stdout = (proc.stdout or "")[:_MAX_OUTPUT]
            stderr = (proc.stderr or "")[:_MAX_OUTPUT]
            output = stdout
            if stderr:
                output += f"\n--- stderr ---\n{stderr}"
            output = output.strip() or "(no output)"
            if proc.returncode != 0:
                return f"Erreur d'exécution:\n{output}"
            return output
        except subprocess.TimeoutExpired:
            return "Erreur d'exécution: timeout (30s)"
        except Exception as e:
            pass  # fall through to code_interpreter
    # Fallback: use code_interpreter skill
    skill = SKILL_REGISTRY.get("code_interpreter")
    if skill:
        result = await skill.run(code=code)
        return result.output if result.success else f"Erreur d'exécution:\n{result.error}"
    return "code_interpreter skill non disponible."

discover_skills()

# Matches queries that are purely math: "2 + 2", "sqrt(144)", "3**10", "combien font 5*7", etc.
_MATH_QUERY_RE = re.compile(
    r"^(?:"
    r"(?:combien\s+(?:font|fait|vaut|vale|égal(?:e)?|=)\s+)?"  # French prefix
    r"(?:calcule?\s+|compute\s+|evaluate\s+|eval\s+)?"         # optional verb
    r")?"
    r"(?P<expr>"
    r"[\d\s\+\-\*\/\%\(\)\.\,\^]+"                             # basic arithmetic chars
    r"|(?:sqrt|sin|cos|tan|log|abs|round|min|max|pi|e)\b.*"    # math function calls
    r")"
    r"[?!.\s]*$",
    re.I,
)


def _extract_math_expr(query: str) -> str | None:
    """Return a calculator-friendly expression if the query is pure math, else None."""
    q = query.strip()
    m = _MATH_QUERY_RE.match(q)
    if not m:
        return None
    expr = m.group("expr").strip().replace("^", "**").replace(",", ".")
    # Require at least one digit or named constant to avoid matching words
    if not re.search(r"[\d]|pi\b|e\b", expr):
        return None
    # Reject if it looks like a sentence (contains alpha words > 2 chars that aren't math fns)
    _MATH_FNS = {"sqrt", "sin", "cos", "tan", "log", "abs", "round", "min", "max", "pi"}
    words = re.findall(r"[a-zA-Z]{3,}", expr)
    if any(w.lower() not in _MATH_FNS for w in words):
        return None
    return expr


# ── Détection directe — bypass LLM total pour les commandes système ───────────
DIRECT_PATTERNS = [
    (re.compile(r"(?:ouvre|lance|démarre|open|start)\s+(\w+)", re.I),
     lambda m: ("open_app", {"app": m.group(1)})),
    (re.compile(r"(?:volume|son)\s+(?:à|a|=)?\s*(\d+)", re.I),
     lambda m: ("set_volume", {"level": int(m.group(1))})),
    (re.compile(r"(?:monte|augmente)\s+le\s+(?:volume|son)", re.I),
     lambda m: ("set_volume", {"level": 80})),
    (re.compile(r"(?:baisse|diminue)\s+le\s+(?:volume|son)", re.I),
     lambda m: ("set_volume", {"level": 20})),
    (re.compile(r"(?:coupe|mute)\s+le\s+(?:volume|son|micro)", re.I),
     lambda m: ("set_volume", {"mute": True})),
    (re.compile(r"(?:verrouille|lock)\s*(?:l.écran|le\s+pc|l.ordinateur)?", re.I),
     lambda m: ("system_control", {"action": "lock"})),
    (re.compile(r"(?:éteins?|shutdown|arrête)\s*(?:le\s+pc|l.ordi|l.ordinateur|windows)?", re.I),
     lambda m: ("system_control", {"action": "shutdown"})),
    (re.compile(r"(?:redémarre|restart|reboot)", re.I),
     lambda m: ("system_control", {"action": "restart"})),
    (re.compile(r"(?:veille|sleep|suspend)", re.I),
     lambda m: ("system_control", {"action": "sleep"})),
    (re.compile(r"(?:luminosité|brightness)\s+(?:à|a|=)?\s*(\d+)", re.I),
     lambda m: ("set_brightness", {"level": int(m.group(1))})),
    (re.compile(r"(?:baisse|diminue|réduis?|descends?)\s+(?:la\s+)?(?:luminosité|brightness)\s+(?:de\s+)?(\d+)", re.I),
     lambda m: ("set_brightness", {"delta": -int(m.group(1))})),
    (re.compile(r"(?:monte|augmente|hausse|increases?)\s+(?:la\s+)?(?:luminosité|brightness)\s+(?:de\s+)?(\d+)", re.I),
     lambda m: ("set_brightness", {"delta": int(m.group(1))})),
    (re.compile(r"(?:baisse|diminue|réduis?)\s+(?:la\s+)?(?:luminosité|brightness)\b", re.I),
     lambda m: ("set_brightness", {"delta": -20})),
    (re.compile(r"(?:monte|augmente|hausse)\s+(?:la\s+)?(?:luminosité|brightness)\b", re.I),
     lambda m: ("set_brightness", {"delta": 20})),
    # Generic web search (API, fast) — no browser keyword
    (re.compile(r"(?:cherche|recherche|google|trouve|search|quoi de neuf sur|news sur|infos sur)\s+(.+)", re.I),
     lambda m: ("web_search", {"query": m.group(1)})),
    (re.compile(r"(?:liste|list|affiche|montre|show)\s+(?:les\s+)?(?:fichiers?|dossiers?|files?)\s+(?:dans|in|de|of|du|à|at)\s+(.+)", re.I),
     lambda m: ("list_files", {"path": m.group(1).strip()})),
    (re.compile(r"(?:lis|lire|read|ouvre|open)\s+(?:le\s+fichier\s+|fichier\s+)?['\"]?([\w/\\.~-]+\.\w+)['\"]?", re.I),
     lambda m: ("read_file", {"path": m.group(1).strip()})),
]

_SCREEN_OCR_RE = re.compile(
    r"\b(?:"
    r"(?:lis|lire|extrai[st]?|read|extract)\s+(?:le\s+)?texte\s+(?:(?:de|sur|à)\s+)?(?:l[' ])?écran|"
    r"(?:qu[' e]+est[- ]ce\s+(?:qu[' e]+il\s+y\s+a|qui\s+(?:est\s+)?(?:écrit|affiché))\s+(?:sur\s+)?(?:l[' ])?écran)|"
    r"(?:fais?\s+(?:une?\s+)?)?(?:capture|screenshot|screen[- ]shot)|"
    r"(?:ocr[\s:]+(?:(?:l[' ])?écran|screen))|"
    r"(?:screen\s+(?:ocr|capture|read))"
    r")\b",
    re.I,
)

_SCREEN_DESCRIBE_RE = re.compile(
    r"\b(?:"
    r"(?:(?:décri[st]?|describe|dis[- ]?moi\s+ce\s+(?:que\s+tu\s+vois|qu(?:'|e\s+)il\s+y\s+a))\s+(?:(?:sur\s+)?(?:l[' ])?écran|(?:my\s+)?screen))|"
    r"(?:(?:regarde|look\s+at)\s+(?:(?:mon\s+)?écran|my\s+screen))|"
    r"(?:qu(?:'|e\s+)est[- ]ce\s+que\s+tu\s+vois)"
    r")\b",
    re.I,
)


_DIGEST_RE = re.compile(
    r"\b(?:"
    r"digest|briefing|morning[- ]brief|"
    r"qu(?:'|e\s+)est[- ]ce\s+qui\s+se\s+passe(?:\s+dans\s+le\s+monde)?|"
    r"quoi\s+de\s+neuf(?:\s+dans\s+le\s+monde)?|"
    r"(?:les\s+)?(?:nouvelles|actualités|news)\s+du\s+(?:jour|matin|monde)|"
    r"r[eé]sum[eé]\s+(?:du\s+jour|de\s+la\s+journ[eé]e|du\s+matin)|"
    r"(?:lance|affiche|donne[- ]?moi|montre)[- ]?(?:moi\s+)?(?:le\s+)?(?:digest|brief|r[eé]sum[eé])|"
    r"what'?s\s+(?:happening|new)\s+(?:in\s+the\s+world|today)"
    r")\b",
    re.I,
)

_SCHEDULE_DIGEST_RE = re.compile(
    r"(?:programme|planifie|schedule|mets?\s+en\s+place|configure)"
    r".*?(?:digest|briefing|morning[- ]brief)"
    r".*?(?:à|a|at|pour|for)\s+(\d{1,2})[h:](\d{0,2})",
    re.I,
)

_SKILL_DIRECT_RE = re.compile(r"^skill:([\w\-]+)(?:\s+--?)?\s*(.*)", re.S | re.I)

_CAPABILITIES_RE = re.compile(
    r"(?:^/skills?\s*$)"
    r"|\b(?:"
    r"(?:qu(?:e\s+)?(?:peux|sais)[- ]?tu\s+faire|what\s+can\s+you\s+do)|"
    r"(?:liste(?:r)?|montre(?:r)?|affiche(?:r)?|show|list)\s+(?:(?:tes|tos|your|les|all|mes|vos|my)\s+)?(?:skills?|capacit[eé]s?|fonctionnalit[eé]s?|outils?|tools?|aptitudes?|pouvoirs?|capabilities|skills?\s+install[eé]s?)|"
    r"(?:quelles?\s+sont\s+(?:tes|vos|your)\s+(?:skills?|capacit[eé]s?|fonctionnalit[eé]s?|outils?|capabilities))|"
    r"(?:aide|help|commandes?|commands?)\s*$|"
    r"(?:tu\s+(?:peux|sais)\s+faire\s+quoi|what\s+do\s+you\s+(?:do|know))"
    r")\b",
    re.I,
)

_SKILL_CATEGORIES: dict[str, list[str]] = {
    "Web & Browser":  ["web_search", "browser_search", "browser_navigate", "browser_list", "http_request"],
    "System":         ["open_app", "set_volume", "set_brightness", "system_control", "list_files", "read_file", "write_file", "shell"],
    "Intelligence":   ["think", "calculator", "code_interpreter", "repl"],
    "Information":    ["morning_digest"],
    "Memory":         ["memory_store", "memory_search", "memory_recall"],
    "Monitoring":     ["monitor_add", "monitor_list", "monitor_run"],
}


def _format_capabilities() -> str:
    from seraphim.skills.registry import SKILL_REGISTRY
    if not SKILL_REGISTRY:
        from seraphim.skills.registry import discover_skills
        discover_skills()

    all_skills = dict(SKILL_REGISTRY)
    categorized: set[str] = set()
    lines = ["# Seraphim — Capacités\n"]

    # ── Built-in skills ───────────────────────────────────────────────────────
    lines.append("## Skills natifs\n")
    for category, names in _SKILL_CATEGORIES.items():
        present = [n for n in names if n in all_skills]
        if not present:
            continue
        lines.append(f"### {category}")
        lines.append("| Skill | Description |")
        lines.append("|-------|-------------|")
        for n in present:
            desc = all_skills[n].description[:80].replace("|", "\\|")
            lines.append(f"| `{n}` | {desc} |")
            categorized.add(n)
        lines.append("")

    other = [n for n in sorted(all_skills) if n not in categorized]
    if other:
        lines.append("### Autres natifs")
        lines.append("| Skill | Description |")
        lines.append("|-------|-------------|")
        for n in other:
            desc = all_skills[n].description[:80].replace("|", "\\|")
            lines.append(f"| `{n}` | {desc} |")
        lines.append("")

    # ── Catalog skills (openclaw / hermes / skillssh / …) ────────────────────
    try:
        from seraphim.skills.catalog import _load_catalog
        catalog = _load_catalog()
        if catalog:
            from collections import defaultdict
            by_source: dict[str, list] = defaultdict(list)
            for entry in catalog:
                by_source[entry.get("source", "?")].append(entry)

            lines.append("## Skills du catalogue externe\n")
            lines.append("| Source | Nombre | Exemples |")
            lines.append("|--------|--------|---------|")
            for source, entries in sorted(by_source.items()):
                examples = ", ".join(
                    f"`{e['name']}`" for e in entries[:4]
                )
                lines.append(f"| {source} | {len(entries)} | {examples}… |")
            lines.append("")
            lines.append(
                f"> **{len(catalog)} skills** au total dans le catalogue. "
                "Cherche avec : `seraphim skill search <mot-clé>`"
            )
            lines.append("")
    except Exception:
        pass

    total_native = len(all_skills)
    try:
        from seraphim.skills.catalog import get_catalog_size
        total_catalog = get_catalog_size()
    except Exception:
        total_catalog = 0

    lines.append(f"**Total : {total_native} natifs + {total_catalog} catalogue = {total_native + total_catalog} skills.**")
    lines.append("\nCommandes utiles :")
    lines.append("- `seraphim ask \"...\"` — poser une question")
    lines.append("- `seraphim digest run` — morning digest")
    lines.append("- `seraphim monitor add <nom> <condition>` — moniteur continu")
    lines.append("- `seraphim skill search <mot-clé>` — chercher dans le catalogue")
    lines.append("- `seraphim skill sync-all` — mettre à jour le catalogue")
    return "\n".join(lines)


_IDENTITY_BLOCK = (
    "\n\n=== IDENTITY (ABSOLUTE, NON-NEGOTIABLE) ===\n"
    "Your name is Seraphim. You are a personal AI assistant running on this user's local machine.\n"
    "You are NOT Qwen. You are NOT an Alibaba product. You are NOT ChatGPT. You are NOT Claude.\n"
    "If asked who you are: always answer 'I am Seraphim'.\n"
    "NEVER say 'as an AI I have no internet access' — you DO have web search via your tools.\n"
    "NEVER say 'I cannot access external resources' — you CAN search the web.\n"
    "When you searched the web in a previous message, say so clearly: 'I found this by searching the web.'\n"
    "=== END IDENTITY ==="
)


def _build_registry_tool_schemas(query: str = "", max_installed: int = 10) -> list[dict]:
    """Build native tool schemas from SKILL_REGISTRY + top-K relevant installed skills.

    Built-in skills always included; installed skills filtered by query relevance so
    small models (3B) don't choke on a massive tool list.
    """
    schemas = list(get_all_tools())
    seen: set[str] = {s["function"]["name"] for s in schemas}
    try:
        from seraphim.skills.manager import get_skill_manager
        from seraphim.skills.catalog import search_skills
        mgr = get_skill_manager()
        if not len(mgr):
            mgr.discover()
        skill_tools = mgr.get_skill_tools()
        if query and len(skill_tools) > max_installed:
            try:
                relevant_names = {r["name"] for r in search_skills(query, top_k=max_installed)}
                skill_tools = [st for st in skill_tools if st.manifest.name in relevant_names]
            except Exception:
                skill_tools = skill_tools[:max_installed]
        else:
            skill_tools = skill_tools[:max_installed]
        for st in skill_tools:
            schema = st.to_tool_schema()
            name = schema["function"]["name"]
            if name not in seen:
                schemas.append(schema)
                seen.add(name)
    except Exception:
        pass
    return schemas


async def _dispatch_skill_tool_calls(tool_calls: list, query: str) -> str:
    """Execute skills from native LLM function-calling response."""
    results: list[str] = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        args_raw = fn.get("arguments") or {}
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw)
            except Exception:
                args = {"task": args_raw}
        else:
            args = dict(args_raw)

        if name in SKILL_REGISTRY:
            try:
                res = await SKILL_REGISTRY[name].run(**args)
                results.append(res.output if res.success else f"Error: {res.error}")
            except Exception as e:
                results.append(f"Skill error ({name}): {e}")
        else:
            try:
                sub = SkillAgent(name)
                task = args.get("task") or args.get("query") or query
                results.append(await sub.run(str(task)))
            except FileNotFoundError:
                results.append(f"Skill '{name}' not found. Run: seraphim skill sync-all")
            except Exception as e:
                results.append(f"Skill error ({name}): {e}")

    return "\n\n".join(results) if results else "(no result)"


class ChatAgent(BaseAgent):
    name = "chat"
    description = "Conversational agent for general questions and assistance"
    system_prompt = (
        "You are Seraphim, a helpful, concise, and friendly personal AI assistant. "
        "You run entirely on the user's local machine. Be direct, honest, and useful. "
        + _IDENTITY_BLOCK
    )

    async def run(self, query: str, context: AgentContext = None) -> str:
        global _pending_code, _pending_file

        # Execution confirmation — user said yes to running pending code
        if _pending_code and re.match(
            r"^(?:oui|yes|ouais|yep|exécute[- ]?le|run\s+it|lance[- ]?le|vas[- ]?y|go|ok|sure|execute|exécute|lancer)\s*[!.,]?\s*$",
            query.strip(), re.I
        ):
            code, fpath = _pending_code, _pending_file
            _pending_code = None
            _pending_file = None
            return await _run_code(code, fpath)

        # Direct skill invocation — user writes "skill:<name> <query>"
        _sdm = _SKILL_DIRECT_RE.match(query.strip())
        if _sdm:
            _skill_name = _sdm.group(1)
            _sub_query = _sdm.group(2).strip() or query
            try:
                _skill_agent = SkillAgent(_skill_name)
                return await _skill_agent.run(_sub_query)
            except FileNotFoundError:
                return f"Skill '{_skill_name}' non trouvé. Lance : seraphim skill sync-all"
            except Exception as _e:
                return f"Erreur skill '{_skill_name}': {_e}"

        # Bypass LLM — math expressions
        expr = _extract_math_expr(query)
        if expr:
            skill = SKILL_REGISTRY.get("calculator")
            if skill:
                result = await skill.run(expression=expr)
                if result.success:
                    return result.output
                # fall through to LLM if expression was invalid

        # Bypass LLM — capabilities table
        if _CAPABILITIES_RE.search(query.strip()):
            return _format_capabilities()

        # Bypass LLM — screen describe (vision LLM, check before OCR)
        if _SCREEN_DESCRIBE_RE.search(query):
            skill = SKILL_REGISTRY.get("screen_describe")
            if skill:
                result = await skill.run(prompt=query)
                return result.output if result.success else f"Screen describe error: {result.error}"

        # Bypass LLM — screen OCR / capture
        if _SCREEN_OCR_RE.search(query):
            cap_only = bool(re.search(r"\b(?:capture|screenshot|screen[- ]shot)\b", query, re.I))
            if cap_only:
                skill = SKILL_REGISTRY.get("screen_capture")
                if skill:
                    result = await skill.run()
                    return f"Screenshot saved: {result.output}" if result.success else f"Capture error: {result.error}"
            else:
                skill = SKILL_REGISTRY.get("screen_ocr")
                if skill:
                    result = await skill.run()
                    return result.output if result.success else f"OCR error: {result.error}"

        # Bypass LLM — schedule digest
        sm = _SCHEDULE_DIGEST_RE.search(query)
        if sm:
            hour = sm.group(1).zfill(2)
            minute = (sm.group(2) or "00").zfill(2)
            time_str = f"{hour}:{minute}"
            import subprocess as _sp, sys as _sys
            _sp.run([_sys.executable, "-m", "seraphim.cli", "digest", "schedule", "--time", time_str])
            return f"Morning digest scheduled daily at {time_str}. Run `seraphim digest schedule --remove` to cancel."

        # Bypass LLM — morning digest
        if _DIGEST_RE.search(query):
            skill = SKILL_REGISTRY.get("morning_digest")
            if skill:
                result = await skill.run(no_summary=True)
                return result.output if result.success else f"Digest error: {result.error}"

        # Bypass LLM — system commands
        for pattern, builder in DIRECT_PATTERNS:
            m = pattern.search(query)
            if m:
                skill_name, kwargs = builder(m)
                skill = SKILL_REGISTRY.get(skill_name)
                if skill:
                    result = await skill.run(**kwargs)
                    return result.output if result.output else (result.error or "(no output)")

        ctx = self.build_context(query, context)
        tools = _build_registry_tool_schemas(query)
        response, tool_calls = await self._chat_with_tools(ctx.messages, tools)
        if tool_calls:
            return await _dispatch_skill_tool_calls(tool_calls, query)
        ctx.add_assistant(response)
        return response

class CoderAgent(BaseAgent):
    name = "coder"
    description = "Code assistant: debugging, refactoring, explanation, generation"
    system_prompt = (
        "You are Seraphim in coder mode. You are an expert software engineer. "
        "When writing code, prefer clarity over cleverness. "
        "Always explain your reasoning briefly. Use modern best practices. "
        "ALWAYS format your response like this:\n"
        "FILENAME: <suggested_filename.py>\n"
        "```python\n<code here>\n```\n"
        "<brief explanation>\n"
        + _IDENTITY_BLOCK
    )

    async def run(self, query: str, context: AgentContext = None) -> str:
        global _pending_code, _pending_file

        ctx = self.build_context(query, context)
        response = await self._chat(ctx.messages)
        ctx.add_assistant(response)

        # Extract filename suggestion from LLM response
        filename_m = re.search(r"FILENAME:\s*([\w\-\.]+\.py)", response)
        timestamp = datetime.now().strftime("%H%M%S")
        filename = filename_m.group(1) if filename_m else f"seraphim_{timestamp}.py"

        # Extract code block
        code_m = re.search(r"```python\n(.*?)\n```", response, re.DOTALL)
        if not code_m:
            code_m = re.search(r"```\n(.*?)\n```", response, re.DOTALL)

        if not code_m:
            # No code block found — return plain response
            return response

        code = code_m.group(1).strip()
        _pending_code = code

        # Write to ~/seraphim_workspace/
        workspace = Path.home() / "seraphim_workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        file_path = workspace / filename
        file_path.write_text(code, encoding="utf-8")
        _pending_file = str(file_path)

        # Open file in VS Code
        ide_opened = False
        try:
            subprocess.Popen(f'code "{file_path}"', shell=True)
            ide_opened = True
        except Exception:
            pass

        # Strip FILENAME: line from response for clean output
        clean = re.sub(r"^FILENAME:.*\n?", "", response, flags=re.MULTILINE).strip()

        status_lines = [f"\n\n---", f"📁 `{filename}` → `{workspace}`"]
        if ide_opened:
            status_lines.append("🖥️  VS Code ouvert avec le fichier.")
        status_lines.append("\n**Voulez-vous que j'exécute ce code ?** (répondez 'oui' ou 'non')")

        return clean + "\n".join(status_lines)


class ResearcherAgent(BaseAgent):
    name = "researcher"
    description = "Research assistant: summarisation, QA on documents, analysis"
    system_prompt = (
        "You are Seraphim in researcher mode. You specialise in synthesising information, "
        "finding patterns, and producing well-structured, cited answers. "
        + _IDENTITY_BLOCK
    )

    async def run(self, query: str, context: AgentContext = None) -> str:
        ctx = self.build_context(query, context)
        response = await self._chat(ctx.messages)
        ctx.add_assistant(response)
        return response


class ReActAgent(BaseAgent):
    """ReAct agent — thinks and acts using tools."""

    name = "react"
    description = "Agent ReAct — lit des fichiers, cherche sur le web, raisonne étape par étape"
    _auto_trace = False  # manual step-level tracing in run()

    @property
    def system_prompt(self) -> str:
        cwd = Path.cwd().as_posix()
        return (
            "You are Seraphim in ReAct mode. You can use tools to answer the user.\n"
            "To call a tool, write EXACTLY this format (nothing before or after):\n"
            "ACTION: tool_name\n"
            "ARGS: {\"param\": \"value\"}\n\n"
            "Available tools:\n"
            f"- read_file: read a file. Args: {{\"path\": \"{cwd}/file.txt\"}}\n"
            "- write_file: write a file. Args: {\"path\": \"...\", \"content\": \"...\"}\n"
            "- web_search: search the web. Args: {\"query\": \"...\", \"max_results\": 5}\n"
            "- think: reason step-by-step before answering. Args: {\"thought\": \"...\"}\n"
            "- calculator: evaluate math expressions safely. Args: {\"expression\": \"2**10 + sqrt(144)\"}\n"
            "- code_interpreter: run Python code in a subprocess. Args: {\"code\": \"print(1+1)\", \"timeout\": 15}\n"
            "- repl: persistent Python REPL (variables survive between calls). Args: {\"code\": \"x = 42\", \"reset\": false}\n"
            "- http_request: make HTTP requests. Args: {\"url\": \"https://...\", \"method\": \"GET\"}\n"
            "- shell: run any shell/CLI command. Args: {\"command\": \"agent-browser screenshot https://...\", \"timeout\": 60}\n"
            "- browser_navigate: open URL in real browser (Chrome/Edge), read page content or take screenshot. Args: {\"url\": \"https://...\", \"action\": \"read|snapshot|screenshot\", \"browser\": \"auto|chrome|edge|firefox\", \"output_file\": \"shot.png\"}\n"
            "- browser_search: search the web using a real browser. ALWAYS use engine='bing'. For technical topics (code, libraries, software), translate query to English for better results. Args: {\"query\": \"...\", \"engine\": \"bing\", \"browser\": \"auto\"}\n"
            "- browser_list: list installed browsers on this PC. Args: {}\n\n"
            "IMPORTANT RULES:\n"
            "1. Always use forward slashes in paths, never backslashes.\n"
            "2. After receiving a RESULT, give your final answer using ONLY that result.\n"
            "3. Never invent or hallucinate file content or web results. Only use what RESULT contains.\n"
            f"4. The current working directory is: {cwd}\n"
            "5. If user says 'navigateur', 'browser', 'chrome', 'edge', 'firefox', 'bing': use browser_search (engine='bing') or browser_navigate — NOT web_search. NEVER use engine='google' in browser_search — it shows bot warnings.\n"
            "6. For generic web searches (no browser keyword): use web_search first (fast API). Fallback to browser_search if no results.\n"
            "7. For opening/reading specific websites: prefer browser_navigate (handles JS, login state).\n"
            "7. Use think before complex multi-step reasoning to plan your approach.\n"
            "8. Use repl for stateful computation across multiple steps; use code_interpreter for one-shot scripts."
        )

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        global _pending_code, _pending_file

        # Execution confirmation — user confirmed running pending code
        if _pending_code and re.match(
            r"^(?:oui|yes|ouais|yep|exécute[- ]?le|run\s+it|lance[- ]?le|vas[- ]?y|go|ok|sure|execute|exécute|lancer)\s*[!.,]?\s*$",
            query.strip(), re.I
        ):
            code, fpath = _pending_code, _pending_file
            _pending_code = None
            _pending_file = None
            return await _run_code(code, fpath)

        # Direct skill invocation — user writes "skill:<name> <query>"
        _sdm = _SKILL_DIRECT_RE.match(query.strip())
        if _sdm:
            _skill_name = _sdm.group(1)
            _sub_query = _sdm.group(2).strip() or query
            try:
                _skill_agent = SkillAgent(_skill_name)
                return await _skill_agent.run(_sub_query)
            except FileNotFoundError:
                return f"Skill '{_skill_name}' non trouvé. Lance : seraphim skill sync-all"
            except Exception as _e:
                return f"Erreur skill '{_skill_name}': {_e}"

        # ── Détection directe — bypass LLM total ────────────────────────────
        expr = _extract_math_expr(query)
        if expr:
            skill = SKILL_REGISTRY.get("calculator")
            if skill:
                try:
                    result = await skill.run(expression=expr)
                    return result.output
                except Exception as e:
                    return f"Calculator error: {e}"

        # ── Browser search — déterministe + synthèse LLM ───────────────────
        # Pattern: "cherche ... dans le navigateur : <query>"
        #          "recherche sur chrome/edge/bing : <query>"
        _BROWSER_SEARCH_RE = re.compile(
            r"(?:cherche(?:r)?|recherche(?:r)?|trouve(?:r)?|search|infos?|nouvelles?|news)\s+.*?"
            r"(?:dans\s+le\s+navigateur|avec\s+le\s+navigateur|via\s+le\s+navigateur"
            r"|sur\s+(?:chrome|edge|firefox|bing))"
            r"\s*[:\-]?\s*(.+)"
            r"|(?:cherche(?:r)?|recherche(?:r)?|trouve(?:r)?|search|infos?|nouvelles?|news)"
            r"\s*[:\-]\s*(.+)",
            re.I | re.S,
        )
        _BROWSER_KW = re.compile(
            r"\b(navigateur|browser|chrome|edge|firefox|bing)\b", re.I
        )
        if _BROWSER_KW.search(query):
            bm = _BROWSER_SEARCH_RE.search(query)
            search_query = (bm.group(1) or bm.group(2) or "").strip() if bm else query
            # Extract after colon if present: "cherche : python 3.13 news"
            if ":" in search_query:
                search_query = search_query.split(":", 1)[-1].strip()
            browser_skill = SKILL_REGISTRY.get("browser_search")
            if browser_skill and search_query:
                raw = await browser_skill.run(query=search_query, engine="bing", timeout=90)
                tool_output = raw.output if raw.success else f"Erreur: {raw.error}"
                # LLM synthesis of browser results
                ctx = self.build_context(
                    f"Browser search results for '{search_query}':\n\n{tool_output}\n\n"
                    "Based ONLY on these results, give a concise answer in the user's language. "
                    "Cite titles and URLs. Do not invent information not in the results.",
                    context,
                )
                return await self._chat(ctx.messages)

        # ── Capabilities table ───────────────────────────────────────────────
        if _CAPABILITIES_RE.search(query.strip()):
            return _format_capabilities()

        # ── Screen describe ──────────────────────────────────────────────────
        if _SCREEN_DESCRIBE_RE.search(query):
            skill = SKILL_REGISTRY.get("screen_describe")
            if skill:
                result = await skill.run(prompt=query)
                return result.output if result.success else f"Screen describe error: {result.error}"

        # ── Screen OCR / capture ─────────────────────────────────────────────
        if _SCREEN_OCR_RE.search(query):
            cap_only = bool(re.search(r"\b(?:capture|screenshot|screen[- ]shot)\b", query, re.I))
            if cap_only:
                skill = SKILL_REGISTRY.get("screen_capture")
                if skill:
                    result = await skill.run()
                    return f"Screenshot saved: {result.output}" if result.success else f"Capture error: {result.error}"
            else:
                skill = SKILL_REGISTRY.get("screen_ocr")
                if skill:
                    result = await skill.run()
                    return result.output if result.success else f"OCR error: {result.error}"

        # ── Schedule digest ──────────────────────────────────────────────────
        sm = _SCHEDULE_DIGEST_RE.search(query)
        if sm:
            hour = sm.group(1).zfill(2)
            minute = (sm.group(2) or "00").zfill(2)
            time_str = f"{hour}:{minute}"
            import subprocess as _sp, sys as _sys
            _sp.run([_sys.executable, "-m", "seraphim.cli", "digest", "schedule", "--time", time_str])
            return f"Morning digest scheduled daily at {time_str}."

        # ── Morning digest — bypass LLM ──────────────────────────────────────
        if _DIGEST_RE.search(query):
            skill = SKILL_REGISTRY.get("morning_digest")
            if skill:
                result = await skill.run(no_summary=True)
                return result.output if result.success else f"Digest error: {result.error}"

        # Skip web_search DIRECT_PATTERN if browser keyword present
        _skip_web_direct = bool(_BROWSER_KW.search(query))
        if not _skip_web_direct:
            for pattern, builder in DIRECT_PATTERNS:
                m = pattern.search(query)
                if m:
                    skill_name, kwargs = builder(m)
                    skill = SKILL_REGISTRY.get(skill_name)
                    if not skill:
                        return f"Skill '{skill_name}' non trouvé."
                    try:
                        result = await skill.run(**kwargs)
                        return result.output if result.output else (result.error or "(no output)")
                    except Exception as e:
                        return f"Erreur : {e}"

        # ── Injection dynamique des skills du catalogue ──────────────────────
        ctx = self.build_context(query, context)
        extra_blocks: list[str] = []

        # 1. Skills installés (SkillManager — overlay appliqué, priorité correcte)
        try:
            from seraphim.skills.manager import get_skill_manager
            mgr = get_skill_manager()
            if len(mgr) == 0:
                mgr.discover()
            if len(mgr) > 0:
                xml = mgr.get_catalog_xml()
                extra_blocks.append(
                    "\n\n## Skills installés\n"
                    "Pour utiliser un skill installé, écris exactement:\n"
                    "ACTION: skill:<nom-du-skill>\n"
                    'ARGS: {"query": "ta demande précise"}\n\n'
                    + xml
                )
        except Exception:
            logger.debug("SkillManager catalog unavailable", exc_info=True)

        # 2. Skills du catalogue externe (JSON) — pertinents pour la requête
        try:
            from seraphim.skills.catalog import search_skills, format_skill_catalog_block
            relevant = search_skills(query, top_k=15)
            if relevant:
                extra_blocks.append(format_skill_catalog_block(relevant))
        except Exception:
            logger.warning("External skill catalog unavailable", exc_info=True)

        if extra_blocks:
            combined = "".join(extra_blocks)
            for msg in ctx.messages:
                if msg.get("role") == "system":
                    msg["content"] += combined
                    break

        # ── Trace collector ──────────────────────────────────────────────────
        from seraphim.learning.collector import TraceCollector
        _tracer = TraceCollector(self.name, query, getattr(context, "session_id", ""))

        # ── ReAct loop standard pour tout le reste ───────────────────────────
        for _ in range(8):
            response = await self._chat(ctx.messages)

            action_match = re.search(r"ACTION:\s*([\w:.\-/]+)", response)
            args_match   = re.search(r"ARGS:\s*(\{.*?\})", response, re.DOTALL)

            if action_match:
                skill_name = action_match.group(1).strip()
                args = {}
                if args_match:
                    raw = args_match.group(1).replace("\\\\", "\\")
                    try:
                        args = json.loads(raw)
                    except json.JSONDecodeError:
                        pass

                # ── External skill (openclaw / hermes) ───────────────────────
                if skill_name.startswith("skill:"):
                    ext_name = skill_name[6:]
                    try:
                        ext_agent = SkillAgent(ext_name)
                        sub_query = args.get("query", query)
                        tool_output = await ext_agent.run(sub_query)
                    except FileNotFoundError:
                        tool_output = (
                            f"Skill '{ext_name}' non trouvé dans le cache. "
                            "Lance : seraphim skill sync-all"
                        )
                    except Exception as e:
                        tool_output = f"Skill error ({ext_name}): {e}"

                # ── Built-in skill ────────────────────────────────────────────
                else:
                    try:
                        skill = SKILL_REGISTRY[skill_name]
                        result = await skill.run(**args)
                        tool_output = result.output if result.success else f"Error: {result.error}"
                    except KeyError:
                        tool_output = f"Skill '{skill_name}' inconnu."
                    except Exception as e:
                        tool_output = f"Skill error: {type(e).__name__}: {e}"

                _tracer.record_step(skill_name, args, tool_output)

                ctx.messages.append({"role": "assistant", "content": response})
                ctx.messages.append({
                    "role": "user",
                    "content": (
                        f"Tool result ({skill_name}):\n{tool_output}\n\n"
                        "Summarize the above result in plain language. "
                        "Do NOT repeat the raw output. Do NOT say ACTION or ARGS. "
                        "Just give a short, clear answer."
                    )
                })

            else:
                ctx.add_assistant(response)
                _tracer.finish(response, success=True)
                await _tracer.save()
                return response

        _tracer.finish("I was unable to complete the task within the allowed steps.", success=False)
        await _tracer.save()
        return "I was unable to complete the task within the allowed steps."

class BuiltinSkillAgent(BaseAgent):
    """Routes built-in SKILL_REGISTRY skills directly without YAML file lookup."""

    name = "builtin_skill"
    description = "Direct built-in skill invocation"

    def __init__(self, skill_name: str) -> None:
        super().__init__()
        self.skill_name = skill_name
        self.system_prompt = f"You are Seraphim. Use the {skill_name} tool to help the user."

    async def run(self, query: str, context: AgentContext | None = None) -> str:
        skill = SKILL_REGISTRY.get(self.skill_name)
        if skill is None:
            ctx = self.build_context(query, context)
            return await self._chat(ctx.messages)

        try:
            if self.skill_name == "web_search":
                result = await skill.run(query=query)
                if not result.success:
                    return result.output
                ctx = self.build_context(
                    f"You just searched the web for: '{query}'\n\n"
                    f"Search results:\n{result.output}\n\n"
                    "Synthesize these results into a concise, helpful answer in the user's language. "
                    "Start your answer by briefly mentioning that you searched the web (e.g. 'I searched the web and found...'). "
                    "Cite the sources. Do not say you have no internet access — you just proved you do.",
                    context,
                )
                return await self._chat(ctx.messages)

            elif self.skill_name == "calculator":
                expr = _extract_math_expr(query) or query
                result = await skill.run(expression=expr)

            elif self.skill_name == "think":
                think_result = await skill.run(thought=query)
                ctx = self.build_context(
                    f"Reasoning:\n{think_result.output}\n\nNow answer concisely: {query}",
                    context,
                )
                return await self._chat(ctx.messages)

            elif self.skill_name == "code_interpreter":
                code_ctx = self.build_context(
                    "Generate Python code to solve the request below. "
                    "Return ONLY raw Python code, no markdown fences, no explanation:\n" + query,
                    context,
                )
                code = await self._chat(code_ctx.messages)
                import re as _re
                m = _re.search(r"```python\n(.*?)\n```", code, _re.DOTALL)
                if m:
                    code = m.group(1)
                result = await skill.run(code=code)

            else:
                try:
                    result = await skill.run(query=query)
                except TypeError:
                    result = await skill.run(thought=query)

        except Exception as e:
            return f"Skill error ({self.skill_name}): {e}"

        return result.output if result.success else f"Error: {result.error}"


def _parse_atom_feed(xml_text: str) -> str | None:
    """Parse Atom/RSS XML (e.g., arXiv API) into human-readable text. Returns None on failure."""
    try:
        import xml.etree.ElementTree as ET
        # Strip HTTP status line if present ("Status: 200\n\n...")
        body = xml_text
        if body.startswith("Status:"):
            body = body.split("\n", 2)[-1].strip()
        # Handle truncated XML: keep only complete <entry>...</entry> blocks
        last_entry_end = body.rfind("</entry>")
        if last_entry_end != -1 and not body.rstrip().endswith("</feed>"):
            body = body[: last_entry_end + len("</entry>")] + "\n</feed>"
        root = ET.fromstring(body)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall("a:entry", ns)
        if not entries:
            return None
        lines: list[str] = []
        for i, entry in enumerate(entries, 1):
            def _t(tag: str) -> str:
                el = entry.find(tag, ns)
                return (el.text or "").strip().replace("\n", " ") if el is not None else ""
            title = _t("a:title")
            arxiv_id = _t("a:id").split("/abs/")[-1]
            published = _t("a:published")[:10]
            authors = ", ".join(
                (a.find("a:name", ns).text or "").strip()
                for a in entry.findall("a:author", ns)
            )
            summary = _t("a:summary")[:250]
            cats = ", ".join(c.get("term", "") for c in entry.findall("{http://www.w3.org/2005/Atom}category"))
            lines.append(
                f"{i}. [{arxiv_id}] {title}\n"
                f"   Authors: {authors}\n"
                f"   Published: {published}  |  Categories: {cats}\n"
                f"   Abstract: {summary}...\n"
                f"   PDF: https://arxiv.org/pdf/{arxiv_id}"
            )
        return "\n\n".join(lines)
    except Exception:
        return None


class SkillAgent(BaseAgent):
    name = "skill"
    description = "Exécute un skill externe (Hermes / OpenClaw / skills.sh)"

    # Capabilities qui déclenchent le mode ReAct avec outils
    _REACTIVE_CAPS = frozenset({
        "shell:execute", "shell", "bash",
        "network:fetch", "network",
        "filesystem:write",
    })

    def __init__(self, skill_name: str):
        super().__init__()
        self.skill_name = skill_name
        self._skill_dir = self._find_skill_dir()
        self._manifest = self._load_manifest()
        # Compat: raw content pour _run_react
        self._raw_content = self._manifest.markdown_content
        self.system_prompt = self._raw_content

    # ── Recherche du répertoire du skill ──────────────────────────────────────

    def _find_skill_dir(self) -> Path:
        name = self.skill_name

        def _is_skill_dir(d: Path) -> bool:
            return d.is_dir() and (
                (d / "SKILL.md").exists()
                or (d / "skill.md").exists()
                or (d / "skill.toml").exists()
            )

        # SkillManager first — overlay-applied manifests, proper priority
        try:
            from seraphim.skills.manager import get_skill_manager
            mgr = get_skill_manager()
            path = mgr.get_path(name)
            if path and path.exists() and _is_skill_dir(path):
                return path
            # Trigger discovery if manager has nothing yet
            if not mgr.get(name):
                mgr.discover()
                path = mgr.get_path(name)
                if path and path.exists() and _is_skill_dir(path):
                    return path
        except Exception:
            pass

        skills_root = Path("~/.seraphim/skills").expanduser()
        if skills_root.exists():
            for candidate in skills_root.rglob(name):
                if _is_skill_dir(candidate):
                    return candidate

        openclaw_skills = Path("~/.seraphim/skill-cache/openclaw/skills").expanduser()
        if openclaw_skills.exists():
            candidate = openclaw_skills / name
            if _is_skill_dir(candidate):
                return candidate

        hermes_root = Path("~/.seraphim/skill-cache/hermes").expanduser()
        for subdir in ("skills", "optional-skills"):
            hermes_skills = hermes_root / subdir
            if hermes_skills.exists():
                for candidate in hermes_skills.rglob(name):
                    if _is_skill_dir(candidate):
                        return candidate

        skillssh_root = Path("~/.seraphim/skill-cache/skillssh").expanduser()
        for subdir in ("skills", "remote"):
            candidate = skillssh_root / subdir / name
            if _is_skill_dir(candidate):
                return candidate

        raise FileNotFoundError(
            f"Skill '{name}' non trouvé. Lance : seraphim skill sync-all"
        )

    def _load_manifest(self):
        from seraphim.skills.loader import SkillLoader
        return SkillLoader().load(self._skill_dir)

    # Compatibility alias
    def _load_skill_prompt(self) -> str:
        return self._manifest.markdown_content

    def _find_skill_md(self) -> str:
        return self._manifest.markdown_content

    # ── Prérequis ─────────────────────────────────────────────────────────────

    def _check_prerequisites(self) -> str | None:
        """Vérifie les binaires et variables d'env requis. Retourne message d'erreur ou None."""
        import shutil
        import sys as _sys

        meta = self._manifest.metadata
        src_meta = meta.get("openclaw") or meta.get("hermes") or {}

        missing_bins: list[str] = []
        for bin_name in src_meta.get("requires", {}).get("bins", []):
            if shutil.which(bin_name) is None:
                missing_bins.append(bin_name)

        missing_envs: list[str] = []
        primary_env = src_meta.get("primaryEnv")
        if primary_env and not __import__("os").environ.get(primary_env):
            missing_envs.append(primary_env)
        for env_name in src_meta.get("envs", []):
            if not __import__("os").environ.get(env_name) and env_name not in missing_envs:
                missing_envs.append(env_name)

        if not missing_bins and not missing_envs:
            return None

        lines = [f"Skill **{self.skill_name}** nécessite des prérequis manquants:\n"]

        if missing_bins:
            lines.append("**Outils manquants:**")
            install_steps: list[dict] = src_meta.get("install", [])
            is_windows = _sys.platform == "win32"
            for bin_name in missing_bins:
                # Trouver la meilleure instruction d'install
                step = self._best_install_step(bin_name, install_steps, is_windows)
                if step:
                    lines.append(f"  - `{bin_name}` — {step}")
                else:
                    lines.append(f"  - `{bin_name}` — installe manuellement")

        if missing_envs:
            lines.append("\n**Variables d'environnement manquantes:**")
            for env in missing_envs:
                lines.append(f"  - `{env}` — ajoute dans ton .env ou via `$env:{env} = '...'`")

        return "\n".join(lines)

    @staticmethod
    def _best_install_step(bin_name: str, steps: list[dict], is_windows: bool) -> str:
        """Retourne la meilleure commande d'install pour le binaire."""
        preferred = ["winget", "choco", "scoop"] if is_windows else ["brew", "apt", "apt-get"]
        candidates = [s for s in steps if bin_name in s.get("bins", [bin_name])]
        if not candidates:
            candidates = steps

        for kind in preferred:
            for s in candidates:
                if s.get("kind") == kind:
                    return _format_install_cmd(s, is_windows)

        if candidates:
            return _format_install_cmd(candidates[0], is_windows)
        return ""

    # ── Security ──────────────────────────────────────────────────────────────

    def _trust_tier(self):
        from seraphim.skills.security import classify_trust_tier
        source = self._skill_dir.parent.name
        return classify_trust_tier(self._skill_dir, source)

    def _security_warning(self) -> str | None:
        from seraphim.skills.security import (
            classify_trust_tier, validate_capabilities, get_tier_warning
        )
        source = self._skill_dir.parent.name
        tier = classify_trust_tier(self._skill_dir, source)
        blocked = validate_capabilities(self._manifest, tier)
        if blocked:
            return (
                f"⛔ Skill **{self.skill_name}** bloqué — capabilities non autorisées pour tier {tier.name}: "
                + ", ".join(f"`{c}`" for c in blocked)
            )
        return get_tier_warning(tier)

    # ── Exécution ─────────────────────────────────────────────────────────────

    async def run(self, query: str, context: AgentContext = None) -> str:
        manifest = self._manifest

        # 1. Prérequis binaires/env
        prereq_error = self._check_prerequisites()
        if prereq_error:
            return prereq_error

        # 2. Security check
        sec_warn = self._security_warning()
        if sec_warn and sec_warn.startswith("⛔"):
            return sec_warn

        # 3. Pipeline déterministe (skill.toml) — sans LLM
        if manifest.steps:
            from seraphim.skills.executor import SkillExecutor
            executor = SkillExecutor(SKILL_REGISTRY)
            return await executor.execute(
                manifest, query, skill_resolver=self._resolve_sub_skill
            )

        # 4. Skills sans capabilities → LLM simple
        caps = set(manifest.required_capabilities)
        if not (caps & self._REACTIVE_CAPS):
            ctx = self.build_context(query, context)
            result = await self.engine.chat(ctx.messages)
            msgs = result.get("messages", [])
            response = msgs[-1].get("content", "") if msgs else ""
            ctx.add_assistant(response)
            return response

        # 5. Fast-path HTTP — skills avec `curl "URL"` dans le doc (ex: weather, arxiv…)
        #    Évite de passer par le LLM pour construire l'URL : extraction directe + http_request.
        if not manifest.steps and caps & {"shell:execute", "network:fetch"}:
            fast = await self._try_http_fast_path(query)
            if fast:
                return fast

        # 6. Capabilities actives → tool calling natif (avec fallback text-parsing)
        tools = self._build_tools_schema()
        try:
            result = await self._run_tool_calling(query, tools)
        except Exception:
            result = await self._run_react(query, context)
        return result

    async def _try_http_fast_path(self, query: str) -> str | None:
        """Extrait les URL curl du skill doc et tente un appel http_request direct.

        Fast-path sans LLM pour les skills avec curl URLs simples (weather, arxiv…).
        Deux stratégies de substitution :
          1. Placeholder QUERY → mots-clés de la query
          2. Ville-placeholder (London…) → localisation extraite de la query
        """
        import re as _re

        raw_urls = _re.findall(
            r"""curl\s+["']((?:https?://)?[^\s"']+)["']""",
            self._raw_content,
        )
        if not raw_urls:
            return None

        url_template = raw_urls[0]
        if not url_template.startswith("http"):
            url_template = "https://" + url_template

        # ── Stratégie 1 : placeholder QUERY littéral (ex: arxiv, APIs génériques) ──
        if "QUERY" in url_template:
            keywords = _re.sub(
                r"\b(?:papers?|articles?|recherche[rz]?|cherche[rz]?|find|search"
                r"|on|about|sur|de|des|les?|un[e]?|du|au?x?|for|me|show|give|list)\b",
                "",
                query,
                flags=_re.I,
            ).strip()
            keywords = _re.sub(r"\s+", "+", keywords).strip("+") or "AI"
            url = url_template.replace("QUERY", keywords)

        # ── Stratégie 2 : placeholder géographique (ex: weather) ──────────────────
        else:
            loc_match = _re.search(
                r"(?:à|a|in|for|at|de|pour|über)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-]{1,30})"
                r"(?:[?!.,]|$)",
                query,
                _re.I,
            )
            if loc_match:
                location = loc_match.group(1).strip().replace(" ", "+")
                url = _re.sub(
                    r"(?:London|Paris|New\+York|Tokyo|Berlin|Madrid|Rome|NYC)",
                    location,
                    url_template,
                    flags=_re.I,
                )
            else:
                url = url_template

        skill = SKILL_REGISTRY.get("http_request")
        if not skill:
            return None
        try:
            result = await skill.run(url=url)
            if not result.success:
                return None
            output = result.output
            # Retire le header "Status: 200\n\n"
            if output.startswith("Status:"):
                parts = output.split("\n", 2)
                output = parts[2].strip() if len(parts) > 2 else output
            # Parse Atom/RSS XML (ex: arxiv) → texte lisible
            if "<feed" in output:
                parsed = _parse_atom_feed(output)
                if parsed:
                    return parsed
            return output if output else None
        except Exception:
            return None

    def _build_tools_schema(self) -> list[dict]:
        """Construit les schémas d'outils à passer au LLM."""
        tools = []
        for name in ("http_request", "shell", "read_file", "write_file", "web_search", "think"):
            skill = SKILL_REGISTRY.get(name)
            if skill:
                tools.append(skill.to_tool())
        return tools

    async def _run_tool_calling(self, query: str, tools: list[dict]) -> str:
        """Tool calling natif via /api/chat — remplace le text-parsing ReAct."""
        import sys as _sys
        cwd = Path.cwd().as_posix()
        win_note = (
            "\nWindows PowerShell environment: use curl.exe (not curl), "
            "double-quote URLs, no && chaining.\n"
            if _sys.platform == "win32" else ""
        )
        system = (
            f"You are Seraphim executing the **{self.skill_name}** skill.\n\n"
            "=== SKILL INSTRUCTIONS ===\n"
            f"{(self._raw_content or '')[:1500]}\n"
            "=== END SKILL INSTRUCTIONS ===\n"
            f"{self._scripts_warning()}"
            f"{win_note}"
            f"Working directory: {cwd}\n"
            "Use http_request tool for API/URL calls. "
            "Call tools directly — do not describe what you would do."
        )

        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ]

        _MAX_ITERS = 8
        _tools_ever_called = False
        for iteration in range(_MAX_ITERS):
            result = await self.engine.chat(messages, tools=tools)
            msg = result.get("messages", [{}])[-1]
            tool_calls = msg.get("tool_calls", [])

            if not tool_calls:
                content = msg.get("content", "")
                # First iteration: model ignored tool definitions → fallback to text-parsing
                if iteration == 0 and not _tools_ever_called:
                    raise RuntimeError("Model does not support tool calling — fallback to ReAct")
                # Final answer after tool use
                return content

            _tools_ever_called = True

            # Append assistant message with tool_calls
            messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tool_calls})

            # Execute each tool call
            for tc in tool_calls:
                fn = tc.get("function", tc)
                tool_name = fn.get("name", "")
                raw_args = fn.get("arguments", {})
                args = raw_args if isinstance(raw_args, dict) else {}

                skill = SKILL_REGISTRY.get(tool_name)
                if skill is None:
                    output = f"Tool '{tool_name}' not found."
                else:
                    try:
                        res = await skill.run(**args)
                        output = res.output if res.success else f"Error: {res.error}"
                    except Exception as exc:
                        output = f"Error: {exc}"

                messages.append({
                    "role": "tool",
                    "content": output[:4000],
                    "name": tool_name,
                })

        raise RuntimeError("Tool calling loop: max iterations — fallback to ReAct")

    async def _resolve_sub_skill(self, skill_name: str, context: dict) -> str:
        try:
            sub_agent = SkillAgent(skill_name)
            return await sub_agent.run(context.get("query", ""))
        except FileNotFoundError:
            return f"[sub-skill '{skill_name}' non trouvé]"
        except Exception as exc:
            return f"[sub-skill '{skill_name}' erreur: {exc}]"

    def _scripts_warning(self) -> str:
        """Retourne un warning si le skill référence scripts/ mais ils ne sont pas installés."""
        scripts_dir = self._skill_dir / "scripts"
        if scripts_dir.exists():
            return ""
        if "scripts/" not in self._raw_content:
            return ""
        return (
            "\n⚠ IMPORTANT: The `scripts/` directory is NOT available in this installation. "
            "Do NOT try to run `python scripts/...` commands — they will fail. "
            "Use direct CLI commands (curl, gh, etc.) or inline python3 -c '...' instead.\n"
        )

    async def _run_react(self, query: str, context: AgentContext | None) -> str:
        """ReAct loop with JSON-mode output — Ollama forces valid JSON regardless of model size."""
        import sys as _sys
        cwd = Path.cwd().as_posix()
        win = _sys.platform == "win32"

        skill_system = (
            "You are an autonomous tool-calling agent. Each response MUST be a single JSON object.\n\n"
            "JSON format:\n"
            '  {"action": "<tool>", "args": {<tool args>}}\n\n'
            "Available tools:\n"
            '  http_request → {"action":"http_request","args":{"url":"https://...","method":"GET"}}\n'
            '  shell        → {"action":"shell","args":{"command":"<cmd>","timeout":30}}\n'
            f'  read_file    → {{"action":"read_file","args":{{"path":"{cwd}/file"}}}}\n'
            '  write_file   → {"action":"write_file","args":{"path":"...","content":"..."}}\n'
            '  web_search   → {"action":"web_search","args":{"query":"..."}}\n'
            '  think        → {"action":"think","args":{"thought":"..."}}\n'
            '  done         → {"action":"done","args":{"result":"<final answer>"}}\n\n'
            "Rules:\n"
            "- Output ONLY the JSON object. No text before or after.\n"
            "- One action per response.\n"
            "- Prefer http_request over shell for API/URL fetching — no curl needed.\n"
            "- Use shell only for local commands (git, python scripts, file ops).\n"
            "- Use 'done' when you have the final answer.\n"
            + (
                "- WINDOWS shell: avoid python3 -c '...'. Use http_request instead of curl.\n"
                if win else ""
            )
            + f"Working dir: {cwd}"
        )

        # Truncate SKILL.md — small models choke on long docs; keep first ~1500 chars
        # (covers description, quick reference, first examples — everything essential)
        _skill_doc = self._raw_content or ""
        _SKILL_MAX = 1500
        if len(_skill_doc) > _SKILL_MAX:
            # Try to cut at a newline boundary
            cut = _skill_doc.rfind("\n", 0, _SKILL_MAX)
            _skill_doc = _skill_doc[: cut if cut > 800 else _SKILL_MAX] + "\n[...skill doc truncated...]"

        skill_context = (
            f"=== SKILL: {self.skill_name} ===\n"
            f"{_skill_doc}\n"
            f"=== END SKILL ===\n"
            f"{self._scripts_warning()}\n"
            f"Task: {query}"
        )

        ctx = AgentContext()
        ctx.add_system(skill_system)
        ctx.add_user(skill_context)

        # JSON schema: force {"action": str, "args": object}
        _json_schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "args":   {"type": "object"},
            },
            "required": ["action", "args"],
        }

        _MAX_ITERS = 8

        for step in range(_MAX_ITERS):
            # Keep context small — system + first user + last 6 messages
            system_msgs = [m for m in ctx.messages if m.get("role") == "system"]
            user_first  = [m for m in ctx.messages if m.get("role") == "user"][:1]
            recent      = ctx.messages[-6:] if len(ctx.messages) > 8 else ctx.messages
            trimmed: list = system_msgs + user_first
            for m in recent:
                if m not in trimmed:
                    trimmed.append(m)

            result = await self.engine.chat(trimmed, format=_json_schema)
            msgs = result.get("messages", [])
            raw_response = msgs[-1].get("content", "") if msgs else ""

            # Parse JSON response
            parsed: dict | None = None
            try:
                parsed = json.loads(raw_response)
            except (json.JSONDecodeError, ValueError):
                # Try extracting JSON from prose fallback
                json_match = re.search(r"\{.*\}", raw_response, re.DOTALL)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group(0))
                    except Exception:
                        pass

            if parsed is None:
                # JSON mode failed entirely → treat as final answer
                ctx.add_assistant(raw_response)
                return raw_response

            action = parsed.get("action", "").strip()
            args: dict = parsed.get("args", {})

            if not action or action == "done":
                result_text = args.get("result") or raw_response
                return result_text

            # Dispatch tool
            if action == "shell" and "timeout" not in args:
                args["timeout"] = 30

            res = None
            try:
                skill = SKILL_REGISTRY[action]
                res = await skill.run(**args)
                tool_output = res.output if res.success else f"Error: {res.error}"
            except KeyError:
                tool_output = f"Tool '{action}' not found. Available: {list(SKILL_REGISTRY.keys())}"
            except Exception as e:
                tool_output = f"Error running {action}: {e}"

            # Post-process: if http_request returned Atom/RSS XML, try native parsing
            if action == "http_request" and res and res.success and "<feed" in tool_output:
                tool_output = _parse_atom_feed(tool_output) or tool_output

            ctx.messages.append({"role": "assistant", "content": raw_response})
            ctx.messages.append({
                "role": "user",
                "content": f"Tool result ({action}):\n{tool_output}\n\nContinue or return done JSON.",
            })

        return "Task reached max steps."

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "chat":       ChatAgent,
    "coder":      CoderAgent,
    "researcher": ResearcherAgent,
    "react":      ReActAgent,
    "skill":      SkillAgent,
}

def get_agent(name: str) -> BaseAgent:
    if name.startswith("skill:"):
        skill_name = name.split(":", 1)[1]
        if not SKILL_REGISTRY:
            discover_skills()
        if skill_name in SKILL_REGISTRY:
            return BuiltinSkillAgent(skill_name)
        return SkillAgent(skill_name)
    cls = AGENT_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(AGENT_REGISTRY.keys())
        raise ValueError(f"Unknown agent '{name}'. Available: {available}")
    return cls()