#!/usr/bin/env python3
"""
Test automation for promo code verification and form submission.

Submits promo forms with automatic reCAPTCHA v3 resolution via 2captcha API.
Configuration via .env file or command-line arguments.
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from replay import load_proxy, load_usage, save_usage
except ImportError:
    def load_proxy(_d, cli):
        return cli
    def load_usage(_d):
        return 0
    def save_usage(_d, _b):
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROMO_HOST = "https://pulsecheck.livetothebeat.org"
API = "https://cdcf-pulse-check.herokuapp.com"
TWOCAPTCHA_API = "https://api.2captcha.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"


def load_env_file(script_dir):
    env_file = os.path.join(script_dir, ".env")
    env_vars = {}
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if line and not line.startswith("#"):
                if "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key.strip()] = val.strip()
    return env_vars


def captured_submission():
    path = os.path.join(SCRIPT_DIR, "full", "requests.jsonl")
    if not os.path.exists(path):
        return {}
    for line in open(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        r = d.get("request", {})
        if r.get("method") == "POST" and r.get("url", "").endswith("/submission"):
            try:
                return json.loads(r.get("postData") or "{}")
            except ValueError:
                return {}
    return {}

def curl(method, url, proxy, headers=None, data=None, timeout=25):
    wfmt = "\n__CODE__%{http_code} __BYTES__%{size_download} %{size_upload} %{size_header} %{size_request}"
    cmd = ["curl", "-s", "-g", "-w", wfmt, "-X", method, "--max-time", str(timeout), "-A", UA]
    if proxy:
        cmd += ["--proxy", proxy]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["--data", data]
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10).stdout
    body, code, nbytes = out, None, 0
    if "__CODE__" in out:
        body, tail = out.rsplit("\n__CODE__", 1)
        code_part, _, bytes_part = tail.partition(" __BYTES__")
        code = code_part.strip()
        for v in bytes_part.split():
            try:
                nbytes += int(float(v))
            except ValueError:
                pass
    return code, body, nbytes


def ask(prompt, default):
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        val = ""
    return val or default


def solve_captcha_2captcha(client_key, website_url, website_key, proxy=None, timeout=60):
    task_payload = {
        "clientKey": client_key,
        "task": {
            "type": "RecaptchaV3TaskProxyless",
            "websiteURL": website_url,
            "websiteKey": website_key,
            "pageAction": "submit"
        }
    }

    print("  -> Sending task to 2captcha...")
    code, body, _ = curl("POST", f"{TWOCAPTCHA_API}/createTask", proxy,
                         headers={"Content-Type": "application/json"},
                         data=json.dumps(task_payload))

    if code != "200":
        print(f"     Error: HTTP {code}")
        return None

    try:
        resp = json.loads(body)
        if resp.get("errorId") != 0:
            print(f"     Error: {resp.get('errorDescription', 'unknown')}")
            return None
        task_id = resp.get("taskId")
        if not task_id:
            print("     Error: No taskId in response")
            return None
    except ValueError:
        print(f"     Error parsing response: {body[:200]}")
        return None

    print(f"     Task created (ID: {task_id}), polling for result...")
    start_time = time.time()
    poll_interval = 5
    consecutive_processing = 0

    while time.time() - start_time < timeout:
        time.sleep(poll_interval)

        result_payload = {"clientKey": client_key, "taskId": task_id}
        code, body, _ = curl("POST", f"{TWOCAPTCHA_API}/getTaskResult", proxy,
                             headers={"Content-Type": "application/json"},
                             data=json.dumps(result_payload))

        if code != "200":
            print(f"     Error: HTTP {code}")
            continue

        try:
            resp = json.loads(body)
            error_id = resp.get("errorId", 0)
            if error_id != 0:
                print(f"     Error: {resp.get('errorDescription', 'unknown')}")
                return None

            status = resp.get("status")
            if status == "ready":
                token = resp.get("solution", {}).get("gRecaptchaResponse")
                if token:
                    print(f"     Token received")
                    return token
                else:
                    print("     Error: No token in response")
                    return None
            elif status == "processing":
                elapsed = int(time.time() - start_time)
                consecutive_processing += 1
                print(f"     Still processing... ({elapsed}s)")
                if consecutive_processing > 12:
                    print("     Timeout: Unable to resolve captcha")
                    return None
            else:
                print(f"     Error: Unknown status {status}")
                return None
        except ValueError as e:
            print(f"     Error: {e}")
            return None

    print(f"     Timeout after {timeout}s")
    return None


def main():
    env_vars = load_env_file(SCRIPT_DIR)

    parser = argparse.ArgumentParser(description="Test promo form submission with automatic reCAPTCHA resolution")
    parser.add_argument("--code", help="Promo code (default: from .env or PCS)")
    parser.add_argument("--proxy", help="HTTP proxy URL")
    parser.add_argument("--no-submit", action="store_true", help="Stop before form submission")
    parser.add_argument("--max-daily-mb", type=float, help="Daily bandwidth limit in MB")
    parser.add_argument("--2captcha-key", help="2captcha API key")
    parser.add_argument("--auto-captcha", action="store_true", help="Auto-solve captcha")
    parser.add_argument("--timeout", type=int, help="Captcha resolution timeout in seconds")
    args = parser.parse_args()

    code = args.code or env_vars.get("PROMO_CODE", "PCS")
    proxy_arg = args.proxy or env_vars.get("PROXY", "")
    proxy = load_proxy(SCRIPT_DIR, proxy_arg if proxy_arg else None)

    max_daily_mb = args.max_daily_mb if args.max_daily_mb is not None else float(env_vars.get("MAX_DAILY_MB", 15.0))
    cap = int(max_daily_mb * 1024 * 1024) if max_daily_mb > 0 else None
    used = load_usage(SCRIPT_DIR)

    auto_captcha = args.auto_captcha or env_vars.get("AUTO_CAPTCHA", "").lower() == "true"
    timeout = args.timeout or int(env_vars.get("CAPTCHA_TIMEOUT", 90))
    recaptcha_key = env_vars.get("RECAPTCHA_KEY", "")

    captured = captured_submission()

    ipcode, ipbody, _ = curl("GET", "https://ipinfo.io/json", proxy, timeout=15)
    try:
        geo = json.loads(ipbody)
        print(f"[*] Exit IP: {geo.get('ip')} ({geo.get('city','?')}, {geo.get('country','?')})")
    except ValueError:
        print("[*] Exit IP: unknown")

    def guard(nbytes):
        nonlocal used
        used += nbytes
        if cap:
            save_usage(SCRIPT_DIR, used)
            if used >= cap:
                print(f"[!] Bandwidth limit reached: {max_daily_mb:.0f} MB/day")
                sys.exit(1)

    print(f"\n[1] GET /promo/{code}")
    c, _, n = curl("GET", f"{PROMO_HOST}/promo/{code}", proxy,
                   headers={"Referer": PROMO_HOST + "/"})
    guard(n)
    print(f"    -> HTTP {c}")

    print(f"[2] POST /promo/verify")
    c, body, n = curl("POST", f"{API}/promo/verify", proxy,
                      headers={"Content-Type": "application/json",
                               "Accept": "application/json, text/plain, */*",
                               "Origin": PROMO_HOST, "Referer": PROMO_HOST + "/"},
                      data=json.dumps({"code": code}))
    guard(n)
    print(f"    -> HTTP {c}")

    token = None
    try:
        token = json.loads(body).get("data", {}).get("token")
    except ValueError:
        pass

    if not token:
        print("[!] Failed to get verification token")
        return
    print("    Token acquired")

    if args.no_submit:
        return

    print("\n[3] Form Input")
    fname = ask("    First name", captured.get("FNAME", ""))
    lname = ask("    Last name", captured.get("LNAME", ""))
    email = ask("    Email", captured.get("EMAIL", ""))

    captcha = ""
    if auto_captcha:
        client_key = args.__dict__.get("2captcha_key") or env_vars.get("TWOCAPTCHA_KEY") or os.environ.get("TWOCAPTCHA_KEY")
        if not client_key:
            print("[!] AUTO_CAPTCHA enabled but no 2captcha key found")
            return
        if not recaptcha_key:
            print("[!] RECAPTCHA_KEY not configured in .env")
            return

        print(f"\n[3.5] Solving reCAPTCHA (timeout: {timeout}s)")
        promo_url = f"{PROMO_HOST}/promo/{code}"
        captcha = solve_captcha_2captcha(client_key, promo_url, recaptcha_key, proxy, timeout=timeout)
        if not captcha:
            print("[!] Failed to solve captcha")
            return
    else:
        print("\n[3.5] Captcha auto-resolution disabled. Set AUTO_CAPTCHA=true to enable")

    payload = {"EMAIL": email, "FNAME": fname, "LNAME": lname, "token": token, "captcha": captcha}

    print(f"\n[4] POST /submission")
    c, body, n = curl("POST", f"{API}/submission", proxy,
                      headers={"Content-Type": "application/json",
                               "Accept": "application/json, text/plain, */*",
                               "Origin": PROMO_HOST, "Referer": PROMO_HOST + "/"},
                      data=json.dumps(payload))
    guard(n)
    print(f"    -> HTTP {c}")

    try:
        resp = json.loads(body)
        if resp.get("message"):
            print(f"    {resp.get('message')}")
    except ValueError:
        pass


if __name__ == "__main__":
    main()
