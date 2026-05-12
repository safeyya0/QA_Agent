# AI QA Agent MVP

Ce projet est un agent autonome alimenté par l'IA (LLM) et Playwright pour tester automatiquement des pages web (ex: pages de login). Il suit un cycle **OBSERVE -> THINK -> ACT -> VERIFY -> REPORT**.

## 🚀 Installation & Démarrage

Suivez ces étapes pour installer et exécuter l'agent localement.

### 1. Prérequis
- Python 3.8+ installé.
- Git installé.

### 2. Cloner le projet
```bash
git clone https://github.com/safeyya0/QA_Agent.git
cd QA_Agent
```

### 3. Configurer l'environnement virtuel et les dépendances
Ouvrez votre terminal dans le dossier `agent_testing` :
```bash
# Créer l'environnement virtuel
python -m venv .venv

# Activer l'environnement virtuel (Windows)
.venv\Scripts\activate


# Installer les dépendances Python
pip install -r requirements.txt

# Installer les navigateurs Playwright
playwright install chromium
```

### 4. Configuration des variables d'environnement
Copiez le fichier exemple et remplissez vos valeurs :
```bash
cp .env.example .env
```
Ouvrez `.env` et remplissez **au minimum** :
```env
GROQ_API_KEY=votre_cle_api_ici   # obligatoire — https://console.groq.com/keys
```
Les autres variables sont optionnelles (Trello, Mission Control, etc.). Consultez `.env.example` pour la liste complète.

### 5. Lancer l'application
Démarrez le serveur FastAPI :
```bash
python main.py
```
Ouvrez votre navigateur et allez sur **http://localhost:8002** pour utiliser l'interface de l'agent.

---

## 🔗 Intégration Mission Control (optionnel)

Mission Control est un dashboard d'orchestration qui permet de piloter OMNISHORE à distance et de lui envoyer des tâches.

### Installation de Mission Control
```bash
git clone https://github.com/builderz-labs/mission-control.git
cd mission-control
pnpm install
pnpm dev
```
Ouvrez **http://localhost:3000** et créez votre compte admin.

### Connecter OMNISHORE
Ajoutez dans votre `.env` :
```env
MISSION_CONTROL_URL=http://localhost:3000
MISSION_CONTROL_USER=admin
MISSION_CONTROL_PASS=votre_mot_de_passe
```
Au démarrage, OMNISHORE s'enregistre automatiquement dans Mission Control sous le nom **omnishore-qa**.

### Envoyer une tâche depuis Mission Control
Dans **Tasks → New Task**, assignez à `omnishore-qa` et mettez en description :
```json
{"url": "https://monsite.com", "spec": "specs/mon_spec.md", "browsers": "chromium"}
```
OMNISHORE exécute les tests et génère le rapport Word automatiquement.

---

## 🛠️ Guide de Contribution pour l'équipe (Git Workflow)

Afin d'éviter les conflits et de garder le code propre, nous utilisons un système de **Branches**. Ne travaillez jamais directement sur la branche `main`.

### Étape 1 : Créer sa propre branche
Avant de commencer à coder une nouvelle fonctionnalité, assurez-vous d'être à jour et créez une branche :
```bash
# Se mettre à jour avec la version principale
git checkout main
git pull origin main

# Créer une branche (utilisez un nom descriptif, ex: feature/trello, fix/ui-bug)
git checkout -b feature/nom-de-ma-fonctionnalite
```

### Étape 2 : Coder et Tester
Développez votre fonctionnalité. Testez-la localement pour vous assurer que rien n'est cassé.

### Étape 3 : Sauvegarder et Envoyer
```bash
# Ajouter les fichiers modifiés
git add .

# Créer un commit descriptif
git commit -m "feat: ajout de l'intégration Trello"

# Envoyer votre branche sur le GitHub
git push origin feature/nom-de-ma-fonctionnalite
```

### Étape 4 : Créer une Pull Request (PR)
1. Allez sur la page GitHub du projet.
2. GitHub vous proposera de créer une **Pull Request** pour votre branche récemment poussée. Cliquez dessus.
3. Décrivez ce que vous avez fait.
4. L'encadrant ou un autre membre de l'équipe reverra le code et le fusionnera (Merge) dans `main`.

## 📁 Architecture du projet

- `main.py` : Point d'entrée de l'API FastAPI et du serveur web.
- `agent/` : Cœur de l'agent (Orchestrateur, Observer, Planner, Executor, Reporter).
- `tools/` : Wrappers pour les services externes (Playwright, Groq LLM, Trello).
- `frontend/` : Interface utilisateur web (HTML, CSS, JS).
- `output/` : créé automatiquement au démarrage — contient les screenshots, rapports `.json` et `.docx` (ignoré par git)
- `specs/` : fichiers `.md` de spécifications de tests
- `tools/mission_control.py` : bridge de connexion avec Mission Control
