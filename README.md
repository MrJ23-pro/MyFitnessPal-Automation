# promo-automation

Script pour tester les formulaires promo avec résolution automatique du reCAPTCHA v3 via l'API 2captcha.

## Setup

```bash
cp .env.example .env
# Remplir .env avec ta clé 2captcha et les autres paramètres
python3 replay_promo.py
```

## .env

- `TWOCAPTCHA_KEY` — Clé API 2captcha (https://2captcha.com)
- `RECAPTCHA_KEY` — Clé publique du site (à extraire du HTML)
- `PROMO_CODE` — Code promo à tester (défaut: PCS)
- `AUTO_CAPTCHA` — `true` pour résoudre auto, `false` pour mode manuel
- `CAPTCHA_TIMEOUT` — Timeout en secondes (défaut: 90)
- `PROXY` — URL proxy optionnelle pour changer la géolocalisation
- `MAX_DAILY_MB` — Limite de bande passante par jour (défaut: 15.0)

## Usage

```bash
# Utilise la config du .env
python3 replay_promo.py

# Override via CLI
python3 replay_promo.py --code AUTRE_CODE --timeout 120

# S'arrête avant la soumission (juste récupère le token)
python3 replay_promo.py --no-submit

# Mode manuel (pas de résolution auto du captcha)
python3 replay_promo.py --auto-captcha false
```

Voir `python3 replay_promo.py -h` pour la liste complète des options.

## Prérequis

- Python 3.6+
- `curl` (doit être dans le PATH)
- Compte 2captcha avec crédits

## Notes

- Le `.env` ne doit jamais être commité (déjà dans `.gitignore`)
- Le reCAPTCHA v3 Enterprise peut prendre 30-60s à résoudre
- Si ça timeout, augmente `CAPTCHA_TIMEOUT` ou vérifie que tu as des crédits 2captcha
# MyFitnessPal-Automation
