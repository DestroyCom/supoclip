# Workflow du fork SupoClip

Fichier local (exclu de Git via `.git/info/exclude`) : il ne doit jamais apparaître dans une PR.

## Remotes
- `upstream` = projet d'origine `FujiwaraChoki/supoclip` (lecture seule pour nous)
- `origin`   = notre fork `DestroyCom/supoclip` (là où on pousse)

## Branches
| Branche | Rôle | Règle |
|---|---|---|
| `main` | Miroir exact de `upstream/main` | Jamais de commit direct, seulement `merge --ff-only upstream/main` |
| `feature/*` | Une PR = une branche | Toujours créée depuis `upstream/main`, jamais depuis `dev` |
| `dev` | Intégration perso : `main` + toutes nos features + réglages locaux | Jamais proposée en PR. Sert à faire tourner l'app |

Pourquoi : des PR indépendantes (pas d'empilement) et un `dev` qui contient tout.

## Nouvelle feature
```powershell
git fetch upstream
git checkout -b feature/xxx upstream/main
# ... code, commits (format: feat(scope): ...) ...
git push -u origin feature/xxx          # puis PR vers FujiwaraChoki/supoclip:main
git checkout dev; git merge feature/xxx; git push origin dev
```
Si B dépend de A : `git checkout -b feature/B feature/A`, PR de B avec « dépend de #A »,
puis `git rebase upstream/main` sur B une fois A mergée.

## Resynchroniser avec upstream (souvent !)
À faire avant de commencer une feature, avant d'ouvrir/mettre à jour une PR, et au moins chaque semaine.
```powershell
git fetch upstream
git checkout main; git merge --ff-only upstream/main; git push origin main
git checkout dev; git merge main; git push origin dev
# pour chaque PR encore ouverte :
git checkout feature/xxx; git rebase upstream/main; git push --force-with-lease
```
Quand une PR est mergée upstream : supprimer la branche (`git branch -d feature/xxx; git push origin --delete feature/xxx`).

## Réglages locaux (docker-compose port 8500, etc.)
Uniquement en commits sur `dev` (ex. `chore(local): ...`). Jamais sur une branche `feature/*`.

## Avant d'ouvrir une PR
- La branche part bien de `upstream/main` à jour (rebase fait)
- `git diff upstream/main --stat` ne montre que la feature (pas de docker-compose local, backups `.json`, etc.)
- Tests lancés : backend `pytest`, frontend `pnpm test` / `pnpm lint`
- Commits au format `type(scope): description`
- Pas de fichiers locaux (`FORK_WORKFLOW.md`, `CLAUDE.local.md`)

## PowerShell 5.1
`&&` n'existe pas : utiliser `;` ou une commande par ligne.
