"""
Prompt templates — extracted from ai_core/prompts/.
Language-agnostic identity and context blocks.
"""

# ── Base system prompt (French, like the original Ely) ──

BASE_PROMPT = """Tu es {name}, un agent IA autonome en ligne de commande.
Tu aides l'utilisateur avec des tâches de développement, analyse de code, debugging, et recherche.

**Règles critiques** :
- Tu es un AGENT, pas un générateur de texte. Chaque action doit utiliser un VRAI outil.
- Ne décris jamais ce que tu "ferais" — appelle les outils RÉELLEMENT.
- BATCH : bash_batch pour N commandes, http_batch pour N requêtes, task_parallel pour N analyses parallèles. 1 tool_call vaut mieux que N.
- Commandes destructives (rm, git reset) → demande confirmation.
- Concis : va droit au but. Si tu ne sais pas, dis-le.
- Utilise read_file, list_directory, grep pour explorer le code. web_search pour des infos récentes.
"""

# ── Compaction prompt (summarize Q&A into a situation memory, max 1000 chars) ──

COMPACT_PROMPT = """Résume la conversation ci-dessous en une mémoire de situation concise (max 1000 caractères).

Décris uniquement la situation actuelle :
- Sur quoi l'utilisateur travaille
- Les décisions importantes qui ont été prises
- Le contexte technique pertinent
- Les actions en cours ou à suivre

Ne liste pas chaque message. Fais une synthèse globale, factuelle et utile pour la suite.

Conversation (questions/réponses) :
{history}

Mémoire (1000 caractères max, texte libre, pas de JSON) :"""

# ── Slash command helper prompts ──

SLASH_PROMPTS = {
    "explain": "Explique le code ou le concept suivant de manière claire et pédagogique :",
    "fix": "Analyse ce code et propose une correction pour le bug ou le problème :",
    "refactor": "Refactorise ce code pour l'améliorer (lisibilité, performance, sécurité) :",
    "test": "Écris des tests unitaires pour ce code :",
}
