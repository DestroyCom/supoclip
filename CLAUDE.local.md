# Règles locales du fork (ne pas commiter, ne pas mettre dans les PR)

Le détail est dans `FORK_WORKFLOW.md`. À appliquer SANS que l'utilisateur ait à le redemander :

- `origin` = fork `DestroyCom/supoclip`, `upstream` = `FujiwaraChoki/supoclip`.
- `main` suit `upstream/main` (ff-only). Ne jamais y commiter.
- Toute nouvelle feature/fix : créer `feature/<nom>` **depuis `upstream/main`** (après `git fetch upstream`), pas depuis `dev` ni depuis une autre feature (sauf dépendance réelle, à signaler).
- `dev` = intégration perso (main + features + réglages locaux). Après avoir fini une feature, la merger dans `dev`.
- Réglages locaux (docker-compose, ports, etc.) : commits sur `dev` uniquement, jamais dans une branche feature.
- **Penser à resynchroniser** : au début d'une session ou d'une feature, et avant toute PR, faire `git fetch upstream` et vérifier si `upstream/main` a avancé. Si oui, proposer/faire : ff de `main`, merge de `main` dans `dev`, rebase des features ouvertes.
- Avant de proposer une PR : vérifier que le diff vs `upstream/main` ne contient que la feature, lancer les tests, commits au format `type(scope): description`.
- Ne jamais ajouter `FORK_WORKFLOW.md` / `CLAUDE.local.md` dans un commit.
- Shell Windows PowerShell 5.1 : pas de `&&`.
- Les push, force-push et suppressions de branches distantes se confirment avec l'utilisateur.
