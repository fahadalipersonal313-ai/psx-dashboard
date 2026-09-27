"""ssl_compat.py — Route HTTPS verification through the OS-native trust store.

Why this exists
---------------
On many Windows machines (corporate networks, SSL-inspecting proxies/AV, or a
locally-installed root CA) the certificate chain for sites like
dps.psx.com.pk or the news feeds is anchored to a root that lives in the
**Windows certificate store** but NOT in certifi's bundled CA list. The result
is requests failing with:

    SSLError: CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate

even though a browser on the same machine loads the site fine (the browser
trusts the Windows store).

`truststore` makes Python's TLS layer validate against the OS trust store, so
whatever roots your machine already trusts are honoured. This is a SECURE fix:
certificates are still fully validated for hostname and chain — we are not
disabling verification, only changing which set of trusted roots is used.

If truststore is unavailable for any reason we log a warning and fall back to
the default certifi behaviour (which may still fail on this machine, but we
never silently drop verification).
"""

import logging

log = logging.getLogger("ssl_compat")
_enabled = False


def enable():
    """Idempotently route TLS verification through the OS trust store."""
    global _enabled
    if _enabled:
        return True
    try:
        import truststore
        truststore.inject_into_ssl()
        _enabled = True
        log.info("TLS verification routed through OS-native trust store "
                 "(truststore active).")
        return True
    except Exception as e:  # pragma: no cover - environment dependent
        log.warning("Could not enable OS trust store (%s); falling back to "
                    "certifi. HTTPS may fail if the required root CA is only "
                    "in the Windows store.", e)
        return False
