#!/usr/bin/env python3
"""
replay.py - Rejoue les requetes reseau capturees dans une session Chrome Network Logger.

Lit un fichier requests.jsonl (full/ ou filtered/) et rejoue chaque requete HTTP(S)
avec la meme methode, le meme User-Agent et le meme Referer que l'original, puis
compare le status obtenu au status d'origine.

Usage:
    python3 replay.py                          # rejoue full/requests.jsonl
    python3 replay.py filtered/requests.jsonl  # un autre fichier
    python3 replay.py --no-trackers            # exclut Google/Facebook/analytics
    python3 replay.py --site-only              # garde uniquement le domaine principal
    python3 replay.py --save out/              # sauvegarde chaque reponse dans out/
    python3 replay.py --timeout 30             # timeout curl en secondes (defaut 20)
    python3 replay.py --proxy http://US_HOST:PORT   # rejoue via un proxy/VPN US

Pour que le trafic parte des Etats-Unis, fournis une IP US via --proxy (proxy HTTP,
HTTPS ou SOCKS5, ex: socks5h://user:pass@us-host:1080) ou active un VPN US avant de
lancer le script. Le proxy US doit venir de toi (service commercial, VPN, etc.) ;
le script se contente d'y router les requetes.

Les requetes chrome-extension:// sont ignorees (non reproductibles hors du navigateur).
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from urllib.parse import urlparse

USAGE_FILE = ".replay_usage.json"   # suivi de la conso journaliere (octets)
PROXY_FILE = "proxy.txt"            # proxy lu ici si --proxy absent (garde les creds hors du script)

TRACKER_HOSTS = (
    "google-analytics.com", "googletagmanager.com", "connect.facebook.net",
    "facebook.com", "google.com/recaptcha", "gstatic.com/recaptcha",
    "doubleclick.net", "analytics",
)


def normalize_proxy(raw):
    """Accepte http://user:pass@host:port OU le format Evomi host:port:user:pass
    (avec ou sans schema). Retourne une URL proxy utilisable par curl."""
    if not raw:
        return None
    raw = raw.strip()
    scheme = "http://"
    if "://" in raw:
        scheme, raw = raw.split("://", 1)
        scheme += "://"
    # Deja au format user:pass@host:port -> on ne touche pas
    if "@" in raw:
        return scheme + raw
    parts = raw.split(":")
    if len(parts) == 4:  # host:port:user:pass
        host, port, user, pw = parts
        return f"{scheme}{user}:{pw}@{host}:{port}"
    return scheme + raw  # host:port simple


def load_proxy(script_dir, cli_proxy):
    if cli_proxy:
        return normalize_proxy(cli_proxy)
    fp = os.path.join(script_dir, PROXY_FILE)
    if os.path.exists(fp):
        with open(fp) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return normalize_proxy(line)
    return None


def today_str():
    return datetime.date.today().isoformat()


def load_usage(script_dir):
    fp = os.path.join(script_dir, USAGE_FILE)
    if os.path.exists(fp):
        try:
            d = json.load(open(fp))
            if d.get("date") == today_str():
                return int(d.get("bytes", 0))
        except (ValueError, OSError):
            pass
    return 0  # nouveau jour ou fichier absent -> reset


def save_usage(script_dir, total_bytes):
    fp = os.path.join(script_dir, USAGE_FILE)
    try:
        json.dump({"date": today_str(), "bytes": int(total_bytes)}, open(fp, "w"))
    except OSError:
        pass


def hget(headers, *names):
    for n in names:
        if n in headers:
            return headers[n]
    return None


def is_tracker(url):
    return any(t in url for t in TRACKER_HOSTS)


def load_requests(path):
    reqs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            r = d.get("request", {})
            url = r.get("url")
            if not url:
                continue
            reqs.append({
                "method": r.get("method", "GET"),
                "url": url,
                "headers": r.get("headers") or {},
                "orig_status": d.get("response", {}).get("status"),
            })
    return reqs


def safe_name(url, idx):
    p = urlparse(url)
    base = (p.netloc + p.path).replace("/", "_").strip("_") or "root"
    return f"{idx:02d}_{base[:80]}"


def main():
    ap = argparse.ArgumentParser(description="Rejoue les requetes d'une session Chrome Network Logger.")
    ap.add_argument("file", nargs="?", default="full/requests.jsonl",
                    help="Chemin du requests.jsonl (defaut: full/requests.jsonl)")
    ap.add_argument("--no-trackers", action="store_true",
                    help="Exclut Google Analytics / Facebook / reCAPTCHA / GTM")
    ap.add_argument("--site-only", metavar="HOST",
                    help="Garde uniquement les requetes vers ce domaine")
    ap.add_argument("--save", metavar="DIR",
                    help="Sauvegarde le corps de chaque reponse dans DIR")
    ap.add_argument("--timeout", type=int, default=20, help="Timeout curl en secondes")
    ap.add_argument("--follow", action="store_true", help="Suit les redirections (curl -L)")
    ap.add_argument("--proxy", metavar="URL",
                    help="Route via un proxy/VPN (ex US): http://host:port, "
                         "socks5h://user:pass@host:1080, ou format host:port:user:pass. "
                         f"Si absent, lu depuis {PROXY_FILE}.")
    ap.add_argument("--max-daily-mb", type=float, default=15.0,
                    help="Plafond de trafic par jour en Mo (defaut: 15). 0 = illimite.")
    ap.add_argument("--reset-usage", action="store_true",
                    help="Remet le compteur de conso du jour a zero puis quitte.")
    args = ap.parse_args()

    # Se placer dans le dossier du script pour resoudre les chemins relatifs
    script_dir = os.path.dirname(os.path.abspath(__file__))

    if args.reset_usage:
        save_usage(script_dir, 0)
        print("Compteur de conso du jour remis a zero.")
        return

    path = args.file if os.path.isabs(args.file) else os.path.join(script_dir, args.file)
    if not os.path.exists(path):
        sys.exit(f"Fichier introuvable: {path}")

    if args.save:
        os.makedirs(args.save, exist_ok=True)

    proxy = load_proxy(script_dir, args.proxy)
    cap_bytes = int(args.max_daily_mb * 1024 * 1024) if args.max_daily_mb > 0 else None
    used = load_usage(script_dir)
    if cap_bytes:
        print(f"== Quota jour : {used/1048576:.2f} / {args.max_daily_mb:.0f} Mo deja utilises ==")
        if used >= cap_bytes:
            print("   /!\\ Quota journalier deja atteint. Rien n'est rejoue "
                  "(--reset-usage pour repartir a zero).")
            return

    # Verifie l'IP/pays de sortie (via le proxy si fourni) pour confirmer l'origine US
    ipcmd = ["curl", "-s", "--max-time", "15"]
    if proxy:
        ipcmd += ["--proxy", proxy]
    ipcmd.append("https://ipinfo.io/json")
    try:
        info = subprocess.run(ipcmd, capture_output=True, text=True, timeout=25).stdout
        geo = json.loads(info)
        print(f"== Sortie : IP {geo.get('ip')} / {geo.get('city','?')}, "
              f"{geo.get('country','?')} ({geo.get('org','?')}) ==")
        if proxy and geo.get("country") != "US":
            print("   /!\\ Le proxy ne sort PAS des Etats-Unis (country != US).")
    except Exception:
        print("== (impossible de determiner l'IP de sortie) ==")

    reqs = load_requests(path)
    print(f"== {len(reqs)} requetes chargees depuis {os.path.relpath(path, script_dir)} ==\n")

    n_ok = n_diff = n_skip = n_err = 0
    quota_hit = False
    for idx, req in enumerate(reqs):
        method, url, headers = req["method"], req["url"], req["headers"]
        orig = req["orig_status"]

        if cap_bytes and used >= cap_bytes:
            quota_hit = True
            print(f"STOP  (quota {args.max_daily_mb:.0f} Mo/jour atteint) "
                  f"-> {len(reqs)-idx} requetes restantes non rejouees")
            break

        if url.startswith("chrome-extension://"):
            print(f"SKIP  (extension)  {method:5} {url[:70]}")
            n_skip += 1
            continue
        if args.no_trackers and is_tracker(url):
            print(f"SKIP  (tracker)    {method:5} {url[:70]}")
            n_skip += 1
            continue
        if args.site_only and urlparse(url).netloc != args.site_only:
            print(f"SKIP  (hors site)  {method:5} {url[:70]}")
            n_skip += 1
            continue

        ua = hget(headers, "User-Agent", "user-agent") or "Mozilla/5.0"
        ref = hget(headers, "Referer", "referer")

        # -g/--globoff : ne pas interpreter [ ] { } dans l'URL (ex: pixel FB ups[pv], expv2[0])
        # On recupere le code HTTP + les octets transferes (montant + descendant + entetes)
        wfmt = "%{http_code} %{size_download} %{size_upload} %{size_header} %{size_request}"
        cmd = ["curl", "-s", "-g", "-w", wfmt, "-X", method,
               "--max-time", str(args.timeout), "-A", ua]
        if proxy:
            cmd += ["--proxy", proxy]
        if args.follow:
            cmd.append("-L")
        if ref:
            cmd += ["-e", ref]
        if method in ("POST", "PUT", "PATCH"):
            cmd += ["--data", ""]
        if args.save:
            out = os.path.join(args.save, safe_name(url, idx))
            cmd += ["-o", out]
        else:
            cmd += ["-o", os.devnull]
        cmd.append(url)

        req_bytes = 0
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout + 10)
            fields = res.stdout.split()
            code = fields[0] if fields else "ERR"
            # somme des octets (download + upload + entetes envoyees/recues)
            for v in fields[1:]:
                try:
                    req_bytes += int(float(v))
                except ValueError:
                    pass
        except subprocess.TimeoutExpired:
            code = "TIMEOUT"

        used += req_bytes
        if cap_bytes:
            save_usage(script_dir, used)

        if code in ("ERR", "TIMEOUT", "000", ""):
            mark, n_err = "x", n_err + 1
        elif orig is None or str(code) == str(orig):
            mark, n_ok = "=", n_ok + 1
        else:
            mark, n_diff = "~", n_diff + 1

        kb = req_bytes / 1024
        print(f"[{code:>4}] (orig {str(orig):>4}) {mark} {kb:6.1f} Ko  {method:5} {url[:60]}")

    print(f"\n== Termine : {n_ok} conformes, {n_diff} status differents, "
          f"{n_err} erreurs, {n_skip} ignorees ==")
    if cap_bytes:
        print(f"== Conso du jour : {used/1048576:.2f} / {args.max_daily_mb:.0f} Mo "
              f"({(cap_bytes-used)/1048576:.2f} Mo restants) ==")
        if quota_hit:
            print("   Quota atteint : relance demain, ou --reset-usage / --max-daily-mb 0.")
    if args.save:
        print(f"   Reponses sauvegardees dans: {args.save}")


if __name__ == "__main__":
    main()
