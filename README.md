# Ely — CLI AI Agent

Agent IA en ligne de commande avec function calling, skills extensibles et sous-agents parallèles.

## Quick start

```bash
pip install -r requirements.txt
cp ely.yaml.example ely.yaml   # édite la clé API
python cli.py
```

## Usage

```bash
python cli.py                      # REPL interactif
python cli.py "explique asyncio"   # Single-shot
python cli.py --context code       # Contexte spécifique
python cli.py --sandbox docker     # Sandbox Docker
python cli.py --tui                # Interface TUI (optionnelle)
```

## REPL

```
Ely · deepseek-v4-flash · ctx: code · bash: direct · 📁 ely-cli
#commande = bash direct | /help = aide | Tab = autocompléter

› #ls -la               # commande bash directe
› /skill activate osint # activer une compétence
› analyse ce fichier    # message → agent
```

| Input | Action |
|---|---|
| `#cmd` | Bash direct (pas de LLM) |
| `Tab` | Autocomplétion |
| `/help` | Toutes les commandes |

## Skills

Dossiers dans `~/.ely/skills/` ou `./skills/` :

```
mon-skill/
├── SKILL.md          # Instructions pour l'agent
├── tools/            # Outils Python
├── references/       # Documentation
└── assets/           # Templates
```

Outil template :
```python
NAME = "docker_push"
DESCRIPTION = "Push une image Docker"
PARAMETERS = {"image": {"type": "string", "description": "Nom"}}
COMMAND = "docker push {image}"
```

Outil Python :
```python
def run(tool_bash, **kwargs):
    output = tool_bash(f"wc -l {kwargs['path']}")
    return f"Lines: {output.split()[0]}"
```

## Configuration

```yaml
provider:
  type: openai
  model: gpt-4o-mini
  url: https://api.openai.com/v1
  api_key: "sk-..."

tools:
  bash_sandbox: direct   # direct ou docker
  disabled: ""           # "bash, bash_batch" → sans shell

mcp:
  servers: []            # Serveurs MCP (stdio ou SSE)
```

## Structure

```
ely/
├── agent.py         # Boucle function calling
├── tools.py         # 21 outils natifs
├── subagent.py      # Sous-agents parallèles
├── skills.py        # Skills directory-based
├── mcp.py           # Client MCP
├── contexts.py      # Contextes persistés
├── memory.py        # Mémoire + compaction
└── providers.py     # OpenAI, Ollama, LM Studio
```
