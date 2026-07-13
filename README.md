# Ely — Standalone CLI AI Agent

Agent IA en ligne de commande avec TUI cockpit, extrait de l'Ely Copilot d'Elyria.

## Installation

```bash
cd ely-cli
pip install -r requirements.txt
```

## Configuration

Crée `~/.ely/config.yaml` ou `./ely.yaml` :

```yaml
provider:
  type: openai
  model: gpt-4o-mini
  url: https://api.openai.com/v1
  api_key: "sk-..."
```

Ou `export OPENAI_API_KEY=sk-...`

### Providers
- **openai** — OpenAI, Azure, DeepSeek, ou toute API compatible
- **ollama** — Modèles locaux (`url: http://localhost:11434`)
- **lmstudio** — LM Studio local (`url: http://localhost:1234/v1`)

## Usage

```bash
# Mode cockpit TUI (défaut)
python cli.py

# Question unique
python cli.py "explique comment fonctionne asyncio"

# Mode REPL simple
python cli.py --no-tui

# Mode Pro
python cli.py --pro "analyse ce code"

# Bash en sandbox Docker
python cli.py --sandbox docker
```

### Raccourcis TUI cockpit

| Touche | Action |
|--------|--------|
| `Enter` | Envoyer |
| `Ctrl+P` | Pro / Flash |
| `Ctrl+C` | Cycle contexte |
| `Ctrl+L` | Effacer l'écran |
| `Ctrl+Q` | Quitter |
| `F1` | Aide |

### Commandes slash

`/explain` `/fix` `/refactor` `/test` `/context` `/pro` `/flash` `/tokens` `/clear`

## Débrayabilité bash : sandbox vs direct

Le bash tool peut s'exécuter en mode **direct** (sur la machine hôte) ou **sandbox** (conteneur Docker isolé).

```bash
# Au lancement
python cli.py --sandbox docker

# Dans l'agent, via un tool
toggle_sandbox(mode="docker")
toggle_sandbox(mode="direct")
```

Config permanent dans `ely.yaml` :
```yaml
tools:
  bash_sandbox: docker   # ou "direct"
```

## Structure

```
ely-cli/
├── cli.py              # Entrée CLI (TUI cockpit, REPL, single-shot)
├── ely/
│   ├── agent.py         # Boucle agent + function calling
│   ├── config.py        # Configuration YAML + env vars
│   ├── providers.py     # OpenAI, Ollama, LM Studio
│   ├── tools.py         # 8 outils (bash, fichiers, web, sandbox)
│   ├── tui.py           # Interface cockpit Textual
│   ├── prompts.py       # Templates de prompts
│   ├── guard.py         # Filtre anti-injection
│   ├── memory.py        # Mémoire + compaction LLM
│   ├── skills.py        # Chargement de skills markdown
│   └── skills/
│       └── ely.md       # Skill principal
├── requirements.txt
└── ely.yaml.example
```

## Différences avec l'Elyria Copilot

- **TUI cockpit** — interface terminal sobre et efficace (Textual)
- **Pas de base de données** — mémoire en fichiers JSON
- **Pas de FastAPI** — pas de serveur web
- **Pas d'auth** — usage local
- **Outils génériques** — bash, fichiers, web
- **Débrayabilité sandbox** — toggle Docker/direct au runtime
- **Code épuré** — ~900 lignes vs ~5000+ dans Elyria
