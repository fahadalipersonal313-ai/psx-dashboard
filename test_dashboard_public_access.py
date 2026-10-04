"""Owner-approved public first-load regression without reading any secret."""
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest

class PublicAccessTests(unittest.TestCase):
    def test_fresh_session_reaches_data_load_without_credentials(self):
        source=Path(__file__).with_name('dashboard.py').read_text()
        self.assertNotIn('DASHBOARD_PASSWORD',source)
        self.assertNotIn('_require_password',source)
        # Exercise the actual bootstrap/theme/refresh path right up to data load.
        # Network startup is isolated; access control itself is not mocked.
        prefix=source.split('# Fast first paint:')[0]
        code='''
import os
import streamlit as st
from unittest.mock import patch
class ForbiddenSecrets:
    def __getitem__(self,key):raise AssertionError('Public startup must not read secrets')
with patch('runtime_bootstrap.prepare'), patch.object(st,'secrets',ForbiddenSecrets()), patch.dict(os.environ):
    os.environ.pop('DASHBOARD_PASSWORD',None)
    exec(compile(PREFIX,'dashboard_public_startup','exec'),globals())
st.success('Public dashboard reached data load')
'''.replace('PREFIX',repr(prefix))
        at=AppTest.from_string(code)
        at.query_params['k']='obsolete-test-link'
        at.run(timeout=20)
        self.assertFalse(at.exception,[x.message for x in at.exception])
        self.assertFalse(at.text_input)
        self.assertTrue(any(x.value=='Public dashboard reached data load' for x in at.success))
        self.assertNotIn('k',at.query_params)
        self.assertNotIn('auth_until',at.session_state)
        at.run()
        self.assertFalse(at.exception)
        self.assertFalse(at.text_input)

if __name__=='__main__':unittest.main()
