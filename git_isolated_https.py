"""Unintegrated HTTPS Git transport; not authorization to publish.

The source and its metadata must be trusted and protected from concurrent
modification, as for the local-only prototype. Credentials are scoped to a
single Git subprocess environment; other processes running as the same user
may still be able to inspect that environment. No redirects are allowed.
"""
import base64
import os
import re
import subprocess
from urllib.parse import urlsplit

from git_isolated_offline import IsolatedGit, InvalidDestination


class IsolatedHttpsGit(IsolatedGit):
    def __init__(self, source, destination, *, allowed_root, username, password):
        if (not isinstance(destination, str) or not destination.isascii()
                or re.search(r'[\x00-\x20\x7f\\%]', destination)):
            raise InvalidDestination('invalid HTTPS destination')
        try:
            url = urlsplit(destination)
            port = url.port
            if (url.scheme != 'https' or not url.hostname or url.username is not None
                    or url.password is not None or url.query or url.fragment
                    or '?' in destination or '#' in destination
                    or not url.path.startswith('/') or destination.endswith('/')
                    or any(segment in ('.', '..') for segment in url.path.split('/'))
                    or (port is not None and not 1 <= port <= 65535)):
                raise ValueError()
        except ValueError:
            raise InvalidDestination('invalid HTTPS destination') from None
        if (not isinstance(username, str) or not username or ':' in username
                or not isinstance(password, str) or not password
                or any(c in username + password for c in '\r\n\x00')):
            raise ValueError('invalid credentials')
        # Reuse the strict trusted-source validation by requiring a direct bare
        # source repository; no remote repository is touched during construction.
        super().__init__(source, source, allowed_root=allowed_root)
        self.destination = destination
        self._authorization = 'Authorization: Basic ' + base64.b64encode(
            (username + ':' + password).encode('utf-8')).decode('ascii')

    def _run(self, *args, repository=True):
        command = ['git']
        if repository:
            command += ['--git-dir', str(self.repo)]
        command += ['-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always',
                    '-c', 'protocol.https.allow=always', '-c', 'http.followRedirects=false',
                    '-c', 'http.sslVerify=true', '-c', 'credential.helper=',
                    '-c', 'core.hooksPath=' + os.devnull, *args]
        env = self.env.copy()
        if args and args[0] in ('ls-remote', 'push'):
            env.update({'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.extraHeader',
                        'GIT_CONFIG_VALUE_0': self._authorization})
        try:
            result = subprocess.run(command, env=env, cwd=self._temp.name,
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('isolated git operation failed') from None
        if result.returncode:
            raise RuntimeError('isolated git operation failed')
        return result.stdout
