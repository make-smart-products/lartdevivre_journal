import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import flask
except ImportError:
    flask = None


@unittest.skipIf(flask is None, 'Install requirements.txt to test the optional Vercel endpoint')
class EndpointTests(unittest.TestCase):
    def setUp(self):
        import app
        self.module = app
        self.client = app.app.test_client()
        self.secret = 'a' * 32
        self.env = patch.dict(os.environ, {'CRON_SECRET': self.secret, 'JOURNAL_GITHUB_TOKEN': 'fake-repo-token', 'TELEGRAM_BOT_TOKEN': 'fake-bot-token', 'JOURNAL_PUBLISH_ENABLED': 'true'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def auth(self):
        return {'Authorization': 'Bearer ' + self.secret}

    def test_unauthorized_request_cannot_publish(self):
        with patch.object(self.module, 'execute') as execute:
            response = self.client.get('/api/publish')
        self.assertEqual(401, response.status_code)
        execute.assert_not_called()

    def test_disabled_migration_cannot_publish(self):
        with patch.dict(os.environ, {'JOURNAL_PUBLISH_ENABLED': 'false'}), patch.object(self.module, 'execute') as execute:
            response = self.client.get('/api/publish', headers=self.auth())
        self.assertEqual(503, response.status_code)
        execute.assert_not_called()

    def test_authenticated_validation_does_not_publish(self):
        with patch.object(self.module, 'execute', return_value={'validated': True}) as execute:
            response = self.client.get('/api/publish?dry_run=true', headers=self.auth())
        self.assertEqual(200, response.status_code)
        execute.assert_called_once_with(dry_run=True)
        self.assertEqual('no-store', response.headers['Cache-Control'])

    def test_configured_cron_invokes_publisher(self):
        with patch.object(self.module, 'execute', return_value={'status': 'published'}) as execute:
            response = self.client.get('/api/publish', headers=self.auth())
        self.assertEqual(200, response.status_code)
        execute.assert_called_once_with(dry_run=False)

    def test_error_never_exposes_secrets(self):
        with patch.object(self.module, 'execute', side_effect=RuntimeError('fake-bot-token fake-repo-token')):
            response = self.client.get('/api/publish', headers=self.auth())
        self.assertEqual(500, response.status_code)
        self.assertNotIn('fake-bot-token', response.get_data(as_text=True))
        self.assertNotIn('fake-repo-token', response.get_data(as_text=True))
