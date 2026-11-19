import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from pysteamauth.base import BaseCookieStorage

import generate


class LoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_cookie_file_is_replaced_and_reported_for_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            cookies_path = Path(directory) / 'cookies.json'
            cookies_path.touch()
            output_path = Path(directory) / 'output'
            steam = AsyncMock()
            # Stop before modifying an SDK repo, after authentication completes.
            steam.request_raw.side_effect = RuntimeError('downloads unavailable')

            def create_steam(**kwargs):
                async def login():
                    kwargs['cookie_storage'].cookies['user'] = {
                        'partner.steamgames.com': {'sessionid': 'new'},
                    }
                steam.login_to_steamworks.side_effect = login
                return steam

            env = {
                'STEAM_LOGIN': 'user', 'STEAM_PASSWORD': 'password',
                'STEAM_SHARED_SECRET': 'secret', 'GITHUB_OUTPUT': str(output_path),
            }
            with patch.dict(os.environ, env), patch('sys.argv', [
                'generate.py', str(Path(directory) / 'sdk'), '--cookies', str(cookies_path),
            ]), patch('generate.Steam', side_effect=create_steam), self.assertLogs('generate'):
                with self.assertRaises(SystemExit):
                    await generate.main()
            self.assertEqual(json.loads(cookies_path.read_text()), {
                'user': {'partner.steamgames.com': {'sessionid': 'new'}},
            })
            self.assertIn('cookies-saved=1', output_path.read_text())
            steam.login_to_steamworks.assert_awaited_once()
            steam.close.assert_awaited_once()

    async def test_failed_login_does_not_report_cookies_for_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            cookies_path = Path(directory) / 'cookies.json'
            cookies_path.write_text(json.dumps({
                'user': {'partner.steamgames.com': {'sessionid': 'saved'}},
            }))
            output_path = Path(directory) / 'output'
            steam = AsyncMock()
            steam.login_to_steamworks.side_effect = RuntimeError('login failed')
            env = {
                'STEAM_LOGIN': 'user', 'STEAM_PASSWORD': 'password',
                'STEAM_SHARED_SECRET': 'secret', 'GITHUB_OUTPUT': str(output_path),
            }
            with patch.dict(os.environ, env), patch('sys.argv', [
                'generate.py', str(Path(directory) / 'sdk'), '--cookies', str(cookies_path),
            ]), patch('generate.Steam', return_value=steam), self.assertLogs('generate'):
                with self.assertRaises(SystemExit):
                    await generate.main()
            self.assertNotIn('cookies-saved=', output_path.read_text())
            steam.close.assert_awaited_once()


class CookieTests(unittest.TestCase):
    def test_missing_empty_and_invalid_cookies_allow_a_fresh_session(self):
        with tempfile.TemporaryDirectory() as directory:
            cookies_path = Path(directory) / 'cookies.json'
            for content in [None, '', '   ', '{broken', 'null', '[]', '{}']:
                with self.subTest(content=content):
                    if content is not None:
                        cookies_path.write_text(content)
                    storage = BaseCookieStorage()
                    generate.load_cookies(storage, cookies_path)
                    self.assertEqual(storage.cookies, {})

    def test_existing_cookies_are_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            cookies_path = Path(directory) / 'cookies.json'
            cookies = {'user': {'partner.steamgames.com': {'sessionid': 'saved'}}}
            cookies_path.write_text(json.dumps(cookies))
            storage = BaseCookieStorage()
            generate.load_cookies(storage, cookies_path)
            self.assertEqual(storage.cookies, cookies)


if __name__ == '__main__':
    unittest.main()
