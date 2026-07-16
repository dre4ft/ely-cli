# Ely — CLI AI Agent

Agent IA en ligne de commande avec function calling, skills, sous-agents parallèles et sandbox.

## Quick start

```bash
pip install -r requirements.txt
cp ely.yaml.example ely.yaml   # édite la clé API

# Linux: filesystem sandbox (optionnel)
sudo apt install bubblewrap     # Debian/Ubuntu
sudo dnf install bubblewrap     # Fedora
# macOS: sandbox-exec intégré

python cli.py
```

## Usage

```bash
python cli.py                      # REPL
python cli.py "explique asyncio"   # Single-shot
python cli.py --context code       # Contexte
python cli.py --sandbox docker     # Sandbox Docker
python cli.py --classic            # UI classique
```

## REPL

| Input | Action |
|---|---|
| `texte` | Envoyé à l'agent |
| `?question` | LLM rapide sans tools |
| `#cmd` | Bash direct (pas de LLM) |
| `/help` | Commandes |
| `Tab` | Autocomplétion |

## Features

**Sub-agents** : `task`, `task_parallel`, `plan` — parallélisation avec contexte minimal.

**Batch** : `bash_batch`, `http_batch` — 1 tool call pour N commandes.

**Skills** : dossiers `~/.ely/skills/<nom>/` avec `SKILL.md` + `tools/` + `references/`. Un seul actif à la fois.

**File tools** : `read_file`, `write_file`, `edit_file` (replace_line, replace_range, replace_text, insert_after, delete_range), `list_directory`, `grep`.

**Sécurité** : `..` bloqué en bash, guard anti-injection, sandbox Docker, désactivation de tools par config.

**Mémoire** : compaction auto tous les 10 cycles. **Diary** : persistant, user-driven. **Contextes** : persistés, custom.

**MCP** : stdio + SSE, `ely.yaml` → `mcp.servers`.

## Configuration

```yaml
provider:
  type: openai             # openai, ollama, lmstudio
  model: gpt-4o-mini
  api_key: "sk-..."

tools:
  workspace: "."
  bash_sandbox: direct
  disabled: ""             # "bash, bash_batch" → sans shell
```

## Structure

```
ely/
├── agent.py, tools.py, providers.py   # Core
├── subagent.py, planner.py            # Sous-agents
├── skills.py, contexts.py, memory.py  # Skills & état
├── mcp.py, guard.py, prompts.py       # Infra
└── ui.py                              # Rendu

ely_rust/    # Port Rust (cargo build --release)
```
