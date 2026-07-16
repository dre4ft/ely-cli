"""Sub-agent tools — background workers and task planning."""
import json
import threading
import time as _time_module
from . import _action

_background_tasks: dict[int, dict] = {}
_task_id_counter = 0
_task_lock = threading.Lock()


def _get_background_results() -> str:
    completed = []
    with _task_lock:
        for tid, t in list(_background_tasks.items()):
            agent = t.get("agent")
            if agent and agent.done:
                result = agent.result or {"reply": "No result", "actions": [], "tokens": {}}
                completed.append(f"[Task #{tid} completed] {t['desc'][:80]}\n{result.get('reply', '')[:500]}")
                del _background_tasks[tid]
    return "\n\n".join(completed) if completed else ""


@_action("task", "Spawn a sub-agent in BACKGROUND. Returns immediately with a task ID.",
         {"description": {"type": "string", "description": "Task description."},
          "context": {"type": "string", "description": "Context: default, code, sysadmin, research"}},
         optional=["context"])
def tool_task(description: str, context: str = "default") -> str:
    global _task_id_counter
    try:
        from ..subagent import SubAgent, _update_sub_status, _remove_sub_status
        with _task_lock: _task_id_counter += 1; tid = _task_id_counter
        def _cb(event, data):
            _update_sub_status(tid, data)
            if event == "sub_done": _remove_sub_status(tid)
        agent = SubAgent(description, context=context, status_cb=_cb)
        agent.start()
        with _task_lock: _background_tasks[tid] = {"agent": agent, "desc": description, "started": _time_module.time()}
        return f"Task #{tid} started in background: {description[:100]}\nCall task_poll({tid}) to check, task_list to see all."
    except Exception as e: return f"Sub-agent error: {e}"


@_action("task_poll", "Check the status of a background task.",
         {"task_id": {"type": "integer", "description": "Task ID returned by task()"}})
def tool_task_poll(task_id: int) -> str:
    with _task_lock: t = _background_tasks.get(task_id)
    if not t: return f"Task #{task_id} not found."
    agent = t.get("agent")
    if not agent: return f"Task #{task_id}: internal error."
    if not agent.done:
        elapsed = _time_module.time() - t.get("started", 0)
        return f"Task #{task_id} still running ({elapsed:.0f}s): {t['desc'][:100]}"
    result = agent.result or {"reply": "No result", "actions": [], "tokens": {}}
    with _task_lock: del _background_tasks[task_id]
    out = f"Task #{task_id} completed:\n{result.get('reply', '')}"
    if result.get("actions"): out += f"\n\n[Actions: {', '.join(result['actions'])}]"
    if result.get("tokens", {}).get("total", 0) > 0: out += f"\n[Tokens: {result['tokens']['total']:,}]"
    return out


@_action("task_list", "List all background tasks and their status.", {})
def tool_task_list() -> str:
    with _task_lock:
        if not _background_tasks: return "No background tasks running."
        lines = [f"{len(_background_tasks)} background task(s):"]
        for tid, t in _background_tasks.items():
            agent = t.get("agent")
            status = "done" if (agent and agent.done) else "running"
            elapsed = _time_module.time() - t.get("started", 0)
            lines.append(f"  #{tid} [{status}] ({elapsed:.0f}s): {t['desc'][:80]}")
        return "\n".join(lines)


@_action("task_parallel", "Spawn MULTIPLE sub-agents in background. Returns immediately with task IDs.",
         {"tasks": {"type": "string", "description": "JSON array: [{\"task\": \"desc\", \"context\": \"default\"}]"}})
def tool_task_parallel(tasks: str) -> str:
    global _task_id_counter
    try:
        tasks_list = json.loads(tasks)
        if not isinstance(tasks_list, list): return "Error: tasks must be a JSON array"
    except json.JSONDecodeError: return "Error: invalid JSON"
    ids = []
    for t in tasks_list:
        desc = t.get("task", t.get("description", "Unknown"))
        ctx = t.get("context", "default")
        try:
            from ..subagent import SubAgent, _update_sub_status, _remove_sub_status
            with _task_lock: _task_id_counter += 1; tid = _task_id_counter
            def _cb(e, d, t=tid): _update_sub_status(t, d) if e != "sub_done" else _remove_sub_status(t)
            agent = SubAgent(desc, context=ctx, status_cb=_cb)
            agent.start()
            _update_sub_status(tid, f"[sub] {desc[:60]}...")
            with _task_lock: _background_tasks[tid] = {"agent": agent, "desc": desc, "started": _time_module.time()}
            ids.append(str(tid))
        except Exception as e: ids.append(f"error:{e}")
    return f"{len(ids)} tasks started: IDs {', '.join(ids)}\nUse task_poll(<id>) to check, task_list to see all."


@_action("plan", "Decompose a complex task into sub-tasks, dispatch to parallel sub-agents with minimal context.",
         {"request": {"type": "string", "description": "The full task to decompose and execute."}})
def tool_plan(request: str) -> str:
    from ..planner import estimate_complexity, PLANNER_PROMPT, parse_plan, build_subagent_prompt
    from ..subagent import SubAgentPool

    complexity = estimate_complexity(request)
    if complexity <= 1:
        from ..subagent import SubAgent
        agent = SubAgent(request, context="default", max_turns=4)
        agent.start()
        result = agent.wait(timeout=120)
        if result and result.get("reply"): return f"**Direct execution:**\n\n{result['reply']}"
        return "Failed to execute task."

    try:
        from ..providers import create_provider
        from ..config import get_provider_config
        cfg = get_provider_config("provider")
        provider = create_provider(cfg)
        plan_resp = provider.chat(messages=[{"role": "user", "content": PLANNER_PROMPT.format(request=request)}], tools=None)
        tasks = parse_plan(plan_resp.get("content", ""))
        if not tasks or len(tasks) < 2:
            from ..subagent import SubAgent
            agent = SubAgent(request, context="default", max_turns=5)
            agent.start(); result = agent.wait(timeout=120)
            return f"**Direct execution:**\n\n{result.get('reply', 'No result')}" if result else "Failed."

        pool = SubAgentPool(max_workers=min(len(tasks), 6))
        for task in tasks: pool.submit(build_subagent_prompt(task), context=task.get("context", "default"), max_turns=task.get("max_turns", 4))
        results = pool.wait_all(timeout=120)

        total_tokens = 0
        lines = [f"**Plan executed: {len(tasks)} sub-tasks across {min(len(tasks), 6)} parallel agents**\n"]
        for i, (task, r) in enumerate(zip(tasks, results)):
            reply = r.get("reply", "No result") if r else "Timeout"
            actions = r.get("actions", []) if r else []
            tokens = r.get("tokens", {}).get("total", 0) if r else 0
            total_tokens += tokens
            lines.append(f"### Sub-task {i+1}: {task.get('desc', task.get('description', 'Unknown'))[:100]}")
            lines.append(reply[:600])
            if actions: lines.append(f"  [{' '.join(actions)}]")
            lines.append("")
        lines.append(f"---\nTotal tokens: {total_tokens:,} across {len(tasks)} sub-agents")
        return "\n".join(lines)
    except Exception as e:
        from ..subagent import SubAgent
        agent = SubAgent(request, context="default", max_turns=5)
        agent.start(); result = agent.wait(timeout=120)
        return f"**Fallback (plan failed: {e}):**\n\n{result.get('reply', 'No result')}" if result else f"Error: {e}"
