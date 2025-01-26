import os
import select
import subprocess  # noqa: S404
import sys
from http import HTTPStatus

import gevent
import requests


def test_backend():
    """Just runs the backend code to make sure `python -m rotkehlchen` works"""
    proc = subprocess.Popen(
        # Only works with --logtarget stdout. Figure out why it does not work
        # without it. The message should be printed and logged, so it should not
        # make a difference: https://github.com/rotki/rotki/blob/8830172fe3f46c0ec56f1e32a1c24be67018c1bf/rotkehlchen/api/server.py#L280-L282  # noqa: E501
        ['python', '-m', 'rotkehlchen', '--logtarget', 'stdout'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    last_line = None
    print('STAAAAAAAAAAAAAAAAARTING')  # noqa: T201
    timeout = 120
    if sys.platform == 'darwin':
        timeout = 30  # in macos the backend may take a long time to start
    with gevent.Timeout(timeout):
        try:
            while True:
                lines = []
                all_output = ''
                readable, _, _ = select.select([proc.stdout], [], [], 1)
                if not readable:
                    continue

                output = os.read(proc.stdout.fileno(), 4096).decode('utf-8')
                if not output:
                    continue

                print(f'got {output=}')  # noqa: T201
                all_output += output
                if 'server is running at' in output:
                    break

            lines = all_output.splitlines()
            for line in lines:
                print(f'got {line=}')  # noqa: T201
                if 'rotki is running in __debug__ mode' in line:
                    print('matched running in debug mode')  # noqa: T201
                    continue

                if 'rotki REST API server is running at' in line:
                    print('matched API server is running')  # noqa: T201
                    last_line = line
                    break

                if last_line:
                    break

            url = f'http://{last_line.split()[-4]}/api/1/info'
            response = requests.get(url)
            assert response.status_code == HTTPStatus.OK
            assert 'data_directory' in response.json()['result']

        except gevent.Timeout as e:
            raise AssertionError(
                f'Did not get all expected output in the stdout after {timeout} seconds',
            ) from e
        finally:
            proc.terminate()
            proc.wait()
