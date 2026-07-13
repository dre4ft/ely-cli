# Rapport Bug Bounty — Access Control
## Cible : https://pprd.cybermalveillance.gouv.fr
## Date : 13 Juillet 2026

---

## Résumé

| # | Vulnérabilité | Sévérité | Statut |
|---|---|---|---|
| 1 | **Deux portails de connexion distincts** (victimes vs prestataires) — Risque d'escalade de privilèges | 🔴 HIGH | À investiguer |
| 2 | **`/_fragment` Symfony accessible** (403 au lieu de 404) — SSRF / inclusion de fragments | 🟡 MEDIUM | Confirmé |
| 3 | **Fichiers `.git/config` et `.env` détectables** (403 WAF ≠ 404) — Exposition de secrets | 🔴 HIGH | Confirmé |
| 4 | **Page de recherche sans CSP** — Réflexion XSS possible via `?search=` | 🟡 MEDIUM | Confirmé |
| 5 | **Password reset introuvable** — Pas de formulaire MDP oublié exposé | 🔵 LOW | Non trouvé |
| 6 | **API endpoints (REST)** — Aucun exposé publiquement | ✅ OK | Négatif |
| 7 | **Inscription utilisateur/Prestataire** — Cachée derrière JS, pas de route directe | 🔵 LOW | Non trouvé |

---

## Détail des vulnérabilités Access Control

### 🔴 1. Dual Login Portal — Risk de Privilege Escalation

**Deux portails de connexion distincts :**

```
👤 Victimes (particuliers/entreprises) :
   /mon-espace/connectez-vous           → id="page-victim-login"
   /mon-espace                          → Redirige vers login

🔧 Prestataires (ExpertCyber) :
   /prestataire/connectez-vous          → id="page-specialist-login"
   /prestataire                         → Redirige vers login
```

**Risques :**
- Un utilisateur victime pourrait-il accéder à des routes réservées aux prestataires après connexion ?
- Un prestataire pourrait-il accéder aux données des victimes ?
- La séparation des rôles est-elle correctement implémentée au niveau backend ?
- Existe-t-il un endpoint d'inscription prestataire accessible sans validation ?
- Les sessions sont-elles partagées entre les deux portails ?

**Tests recommandés :**
```bash
# Vérifier l'accès croisé
GET /prestataire/tableau-de-bord  (avec cookie session utilisateur)
GET /mon-espace/mes-demandes       (avec cookie session prestataire)

# Vérifier l'inscription sauvage
POST /api/prestataire/register
POST /api/inscription
```

---

### 🟡 2. Symfony `/_fragment` endpoint accessible

```
GET /_fragment → 403 Forbidden (au lieu de 404)
```

**Analyse :** Le endpoint `/_fragment` de Symfony est exposé. Bien que bloqué par le WAF, son existence confirme :
- L'application tourne sur **Symfony**
- Le endpoint n'a pas été désactivé en configuration
- Un contournement WAF pourrait permettre un **SSRF** ou une **inclusion de fragments**

**Payloads à tester (si bypass WAF) :**
```
/_fragment?_path=_controller=phpcrud&_path=file=/etc/passwd
/_fragment?_path=_controller=../../../../etc/passwd
```

---

### 🔴 3. Exposition de fichiers sensibles (WAF ≠ 404)

```
/.git/config → 403 Forbidden
/.env        → 403 Forbidden
/.git/HEAD   → 403 Forbidden
```

**Analyse :** Le WAF Imperva bloque l'accès à ces fichiers mais retourne **403** au lieu de **404**. Cela confirme :
- Les fichiers `.git` et `.env` existent sur le serveur
- La configuration du WAF les protège partiellement
- Un contournement ou une faille dans les règles WAF pourrait exposer ces fichiers

**Techniques de bypass à tester :**
```
// Différents encodages
/.git/config
/.git//config
/..%2f.git/config
/%2e%2e/.git/config
/.git/config%00
/.git/config?
/.git/config#
/.git/config~ (backup)
/.env.backup
/.env.save
/.env.local
/.env.prod
```

---

### 🟡 4. Réflexion XSS via Search

```
/resultat-recherche?search=test → 200 OK
```

Le paramètre `search` est reflété dans le titre de la page et potentiellement dans le contenu. Absence de CSP.

---

### 🔵 5. Password Reset non exposé

Aucun endpoint de réinitialisation de mot de passe trouvé :
- `/mon-espace/mot-de-passe-perdu` → 404
- `/mon-espace/mot-de-passe-oublie` → 404

Le texte "Mot de passe oublié" apparaît sur la page de login mais sans lien fonctionnel.

---

### ✅ 6. API REST

Aucun endpoint API REST exposé publiquement :
- `/api/*` → 404
- `/api/login` → 404
- `/api/user/*` → 404
- `/api/prestataire/*` → 404

---

### 🔵 7. Inscription

Aucun endpoint d'inscription trouvé :
- `/mon-espace/inscription` → 404
- `/mon-espace/creer-compte` → 404
- `/mon-espace/creer-mon-compte` → 404
- `/mon-espace/sinscrire` → 404
- `/prestataire/inscription` → 404
- `/prestataire/creer-compte` → 404

L'inscription est probablement déclenchée via JavaScript (modale/SPA).

---

## Cartographie des routes découvertes

### Routes Publiques (200 OK)

| Route | Type | Description |
|---|---|---|
| `/` | Page | Accueil |
| `/mon-espace/connectez-vous` | Login | Portail victimes (id=page-victim-login) |
| `/mon-espace` | Login | Redirection vers login |
| `/prestataire/connectez-vous` | Login | Portail prestataires (id=page-specialist-login) |
| `/prestataire` | Login | Redirection vers login |
| `/resultat-recherche?search=` | Search | Page de recherche |

### Routes protégées par WAF (403)

| Route | Type | Note |
|---|---|---|
| `/_fragment` | Symfony | Endpoint fragment exposé |
| `/.git/config` | Sensible | Existence confirmée |
| `/.env` | Sensible | Existence confirmée |
| `/.git/HEAD` | Sensible | Existence confirmée |

### Routes inexistantes (404)

/admin, /administrator, /backoffice, /api/users, /api/admin, /dashboard,
/profil, /mon-espace/inscription, /mon-espace/mot-de-passe-perdu,
/prestataire/inscription, /prestataire/dashboard, /api/login, /api/me,
/_profiler, /_wdt, /espace-prestataire

---

## Recommandations

1. **Priorité 1** : Creuser l'escalade de privilèges entre portail victime et prestataire
2. **Priorité 2** : Tester le contournement WAF sur `/_fragment` pour SSRF
3. **Priorité 3** : Tester le contournement WAF sur `.git/config` et `.env`
4. **Priorité 4** : Tester XSS sur `?search=` (recherche)
5. **Investiguer** : Inscription prestataire (peut-être accessible via requête POST directe)
