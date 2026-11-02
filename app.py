"""Protected Vercel cron endpoint. No publishing until explicitly enabled."""
import hmac
import os

from flask import Flask, jsonify, request

from cloud.publish import PublishError, execute

app = Flask(__name__)


@app.after_request
def no_cache(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/health')
def health():
    return jsonify(status='ok')


@app.get('/api/publish')
def publish_cron():
    secret = os.environ.get('CRON_SECRET', '')
    if len(secret) < 24:
        return jsonify(error='Cron is not configured'), 503
    if not hmac.compare_digest(request.headers.get('Authorization', '').encode(), ('Bearer ' + secret).encode()):
        return jsonify(error='Unauthorized'), 401
    dry_run = request.args.get('dry_run') == 'true'
    if not os.environ.get('JOURNAL_GITHUB_TOKEN') or not os.environ.get('TELEGRAM_BOT_TOKEN'):
        return jsonify(error='Publishing credentials are not configured'), 503
    if not dry_run and os.environ.get('JOURNAL_PUBLISH_ENABLED') != 'true':
        return jsonify(error='Publishing is disabled until migration is verified'), 503
    try:
        return jsonify(execute(dry_run=dry_run))
    except PublishError:
        # Public responses/logs never echo service URLs, credentials or source data.
        app.logger.warning('Publisher stopped without confirmed completion; check durable state.')
        return jsonify(error='Publication not confirmed; check durable state before retrying'), 503
    except Exception:
        app.logger.error('Publisher encountered an unexpected error; request details suppressed.')
        return jsonify(error='Publication failed; request details suppressed'), 500
