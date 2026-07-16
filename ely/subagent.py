"""
Sub-agents — independent worker agents that execute tasks in parallel.
Each sub-agent runs in its own thread with a focused system prompt and tool access.

Usage:
  pool = SubAgentPool(max_workers=4)
  pool.submit("Read all .py files and count lines", context="code")
  pool.submit("Check disk usage with df -h", context="sysadmin")
  results = pool.wait_all()
"""

import threading


SUBAGENT_PROMPT = """Tu es un sous-agent Ely spécialisé. Tu as TRÈS PEU de tours : {tours} — sois efficace.

**Règles critiques** :
- Tu n'as que {tours} de réflexion. Chaque tour compte. Ne fais PAS d'exploration inutile.
- Va droit au but : identifie l'info nécessaire, utilise l'outil adapté, réponds.
- 1 tour = 1 action. Pas de "je vais d'abord lister, puis lire, puis..." — lis directement le fichier.
- Si tu as besoin de lire UN fichier, fais-le au tour 1. Pas de list_directory + read_file si tu peux juste read_file.
- Après 2 tours sans progrès, force une réponse avec ce que tu as.

**Format de réponse** :
- Commence par le RÉSULTAT directement (pas de blabla introductif)
- En fin de réponse, propose 1-2 AXES D'ÉVOLUTION si pertinent :
  🔄 Évolution possible : [suggestion concrète pour approfondir]

**Règles** :
- Ne demande pas de clarification — fais de ton mieux avec les infos disponibles.
- Réponds dans la langue de la tâche demandée.
- Si tu n'arrives pas à accomplir la tâche, explique pourquoi brièvement.
"""


class SubAgent:
    """A single sub-agent that runs a focused task in a thread."""

    def __init__(self, task: str, context: str = "default", slot: str = "provider",
                 max_turns: int = 5, user_id: str = "subagent", status_cb=None):
        self.task = task
        self.context = context
        self.slot = slot
        self.max_turns = max_turns
        self.user_id = user_id
        self.result = None
        self._thread = None
        self._done = threading.Event()
        self._status_cb = status_cb
        self._name = f"subagent-{user_id}"

    def _run(self):
        """Run the sub-agent in the current thread."""
        from .agent import _resolve_provider, _build_system_prompt
        from .tools import get_tools
        from .config import get_int
        import json

        try:
            # Build minimal system prompt
            system_prompt = SUBAGENT_PROMPT.format(tours=self.max_turns)

            # Add workspace info
            from .tools import get_workspace_info
            system_prompt += f"\n\n**Environnement :** {get_workspace_info()}"

            # Add context
            from .contexts import get_context_prompt
            ctx_prompt = get_context_prompt(self.context)
            system_prompt += f"\n\n**Contexte :** {ctx_prompt}"

            # Resolve provider
            provider, model_name = _resolve_provider(self.slot)

            # Get tools
            tool_defs, tool_handlers = get_tools()

            # Messages
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"**Tâche à accomplir :**\n\n{self.task}"},
            ]

            actions = []
            tokens = {"prompt": 0, "completion": 0, "total": 0}
            reply = ""

            if self._status_cb:
                self._status_cb("sub_thinking", f"[sub] {self.task[:60]}...")

            for turn in range(self.max_turns):
                try:
                    resp = provider.chat(messages, tools=tool_defs if tool_defs else None)
                except Exception as e:
                    reply = f"Erreur: {e}"
                    break

                usage = resp.get("usage", {})
                tokens["prompt"] += usage.get("prompt_tokens", 0)
                tokens["completion"] += usage.get("completion_tokens", 0)
                tokens["total"] += usage.get("total_tokens", 0)

                content = resp.get("content", "") or ""
                tool_calls = resp.get("tool_calls")

                if not tool_calls:
                    reply = content
                    if self._status_cb:
                        self._status_cb("sub_done", f"[sub] ✓ {self.task[:50]}")
                    break

                messages.append({
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": tool_calls,
                })

                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    tc_name = tc["function"]["name"]
                    tc_args_str = tc["function"]["arguments"]

                    try:
                        tc_args = json.loads(tc_args_str)
                    except json.JSONDecodeError:
                        tc_args = {}

                    actions.append(tc_name)
                    if self._status_cb:
                        self._status_cb("sub_tool", f"[sub] 🔧 {tc_name}")

                    handler = tool_handlers.get(tc_name)
                    if handler:
                        try:
                            result = handler(**tc_args)
                        except Exception as e:
                            result = f"Tool error: {e}"
                    else:
                        result = f"Unknown tool: {tc_name}"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": str(result),
                    })

                if turn == self.max_turns - 2:
                    messages.append({
                        "role": "user",
                        "content": "Donne ta réponse finale maintenant. N'appelle plus d'outils.",
                    })

            # Fallback
            if not reply:
                try:
                    resp = provider.chat(messages, tools=None)
                    reply = resp.get("content", "")
                except Exception:
                    reply = "Impossible de générer une réponse."

            self.result = {
                "reply": reply,
                "actions": actions,
                "tokens": tokens,
                "model": model_name,
            }

        except Exception as e:
            self.result = {
                "reply": f"Sub-agent error: {e}",
                "actions": [],
                "tokens": {},
                "model": "unknown",
            }
        finally:
            self._done.set()

    def start(self):
        """Start the sub-agent in a new thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def wait(self, timeout: float = None) -> dict:
        """Wait for the sub-agent to complete and return its result."""
        self._done.wait(timeout)
        return self.result

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def cancel(self):
        """Cancel the sub-agent. Sets a cancelled result."""
        self.result = {
            "reply": "Task cancelled by user.",
            "actions": [],
            "tokens": {},
            "model": "cancelled",
        }
        self._done.set()

    def close(self):
        """Cancel and clean up the sub-agent."""
        self.cancel()


# Shared status for Live display — updated by sub-agents
SUBAGENT_STATUSES: dict[int, str] = {}
_SUB_STATUS_LOCK = threading.Lock()


def _update_sub_status(agent_id: int, status: str):
    with _SUB_STATUS_LOCK:
        SUBAGENT_STATUSES[agent_id] = status


def _remove_sub_status(agent_id: int):
    with _SUB_STATUS_LOCK:
        SUBAGENT_STATUSES.pop(agent_id, None)


def get_sub_statuses() -> dict[int, str]:
    with _SUB_STATUS_LOCK:
        return dict(SUBAGENT_STATUSES)


class SubAgentPool:
    """Manages multiple sub-agents running in parallel."""

    def __init__(self, max_workers: int = 6):
        self.agents: list[SubAgent] = []
        self.max_workers = max_workers
        self._next_id = 0

    def submit(self, task: str, context: str = "default", slot: str = "provider",
               max_turns: int = 5) -> SubAgent:
        """Submit a task to the pool. Starts immediately if under max_workers."""
        agent_id = self._next_id
        self._next_id += 1
        agent = SubAgent(task, context, slot, max_turns, status_cb=lambda ev, d: _update_sub_status(agent_id, d))
        self.agents.append(agent)
        _update_sub_status(agent_id, f"[sub] {task[:60]}...")
        agent.start()
        return agent

    def wait_all(self, timeout: float = 120) -> list[dict]:
        """Wait for all sub-agents to complete. Returns list of result dicts."""
        results = []
        for i, agent in enumerate(self.agents):
            result = agent.wait(timeout=timeout)
            if result is None:
                result = {"reply": f"Timeout after {timeout}s", "actions": [], "tokens": {}, "model": "timeout"}
            result["_task_index"] = i
            results.append(result)
        return results

    def submit_and_wait(self, tasks: list[dict], slot: str = "provider") -> list[dict]:
        """Submit multiple tasks and wait for all. Each task is {task, context}."""
        for t in tasks:
            self.submit(
                t.get("task", t.get("description", "")),
                t.get("context", "default"),
                slot,
                t.get("max_turns", 5),
            )
        return self.wait_all()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.wait_all()
