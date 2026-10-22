import os
import sys
import types

# main.py builds the service account from the environment at import time
for key in ['TYPE', 'PROJECT_ID', 'PRIVATE_KEY_ID', 'PRIVATE_KEY', 'CLIENT_EMAIL', 'CLIENT_ID',
            'AUTH_URI', 'TOKEN_URI', 'AUTH_PROVIDER_X509_CERT_URL', 'CLIENT_X509_CERT_URL', 'UNIVERSE_DOMAIN']:
  os.environ.setdefault(key, 'test')

# rrdtool is installed from apt in the Docker image, so it usually isn't available locally
try:
  import rrdtool
except ImportError:
  sys.modules['rrdtool'] = types.ModuleType('rrdtool')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
