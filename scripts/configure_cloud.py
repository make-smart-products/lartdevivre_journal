"""Configure GitHub Secrets using the user's saved GitHub login, never logging values."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.local-tools'))
BASE = 'https://api.github.com/repos/make-smart-products/lartdevivre_journal'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['secrets', 'dispatch', 'runs', 'logs'])
    parser.add_argument('--run-id', type=int)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    result = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                            capture_output=True, text=True, env=env)
    if result.returncode:
        raise RuntimeError('Saved GitHub login unavailable')
    credentials = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

    def api(path, payload=None, method=None):
        headers = {'Authorization': 'Bearer ' + credentials['password'],
                   'Accept': 'application/vnd.github+json', 'User-Agent': 'LArtDeVivreSetup'}
        data = None if payload is None else json.dumps(payload).encode()
        if data:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            raise RuntimeError('GitHub HTTP ' + str(error.code)) from None
        except OSError:
            raise RuntimeError('GitHub connection failed') from None

    if args.mode == 'secrets':
        from nacl.public import PublicKey, SealedBox
        values = {}
        for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
            match = re.match(r'^\s*(?:export\s+)?(TELEGRAM_BOT_TOKEN)\s*=\s*(.*?)\s*$', line)
            if match:
                values[match[1]] = match[2].strip().strip('\"').strip("'")
        if not values.get('TELEGRAM_BOT_TOKEN'):
            raise RuntimeError('Local Telegram token missing')
        key = api('/actions/secrets/public-key')
        box = SealedBox(PublicKey(base64.b64decode(key['key'])))
        encrypted = base64.b64encode(box.encrypt(values['TELEGRAM_BOT_TOKEN'].encode())).decode()
        api('/actions/secrets/TELEGRAM_BOT_TOKEN', {'encrypted_value': encrypted, 'key_id': key['key_id']}, 'PUT')
        print('TELEGRAM_BOT_TOKEN stored as encrypted GitHub Actions secret.')
    elif args.mode == 'dispatch':
        api('/actions/workflows/publish.yml/dispatches', {'ref': 'main', 'inputs': {'dry_run': 'false' if args.publish else 'true'}}, 'POST')
        print('Cloud publication requested.' if args.publish else 'Cloud validation requested (no Telegram publication).')
    elif args.mode == 'logs':
        import io
        import zipfile
        if not args.run_id:
            raise RuntimeError('Missing run id')
        req = urllib.request.Request(BASE + '/actions/runs/' + str(args.run_id) + '/logs', headers={
            'Authorization': 'Bearer ' + credentials['password'], 'User-Agent': 'LArtDeVivreSetup'})
        with urllib.request.urlopen(req, timeout=30) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        for name in archive.namelist():
            for line in archive.read(name).decode('utf-8', errors='replace').splitlines():
                if any(word in line.lower() for word in ('ошибка:', 'traceback', 'publisherror', 'error:', 'validated')):
                    line = re.sub(r'bot[0-9]+:[A-Za-z0-9_-]+', 'bot[REDACTED]', line)
                    print(line)
    else:
        runs = api('/actions/workflows/publish.yml/runs?per_page=3')
        for run in runs.get('workflow_runs', []):
            print(json.dumps({k: run.get(k) for k in ('id', 'status', 'conclusion', 'html_url', 'event')}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Cloud setup failed. Credentials and request details suppressed.', file=sys.stderr)
        sys.exit(1)
