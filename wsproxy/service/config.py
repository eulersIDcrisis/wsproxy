"""config.py.

Configuration file utilities.
"""


def get_default_config_file_contents():
    """Write out the default file contents for a reference file."""
    return """
# wsproxy configuration file.
---
logging:
  # Set the output for the logger. If 'stderr', then write to stderr.
  file: stderr

# Set as a (nonnegative) integer. Higher numbers imply more verbose
# output. Currently, this goes up to 3.
debug: 0

# Auth Configuration
#
# Superuser authentication parameters here.
username: admin
password: password

# Server Configuration
#
# Configuration options when running as a server. The server is only
# enabled if the 'enabled' key is true.
server:
  # If false, the server is disabled entirely.
  enabled: true

  # Port to run the server on.
  port: 8080

  # Optionally listen on a UNIX socket as well. The `$(pid)` will be
  # substituted with the process ID of the server when spawned.
  unix_socket: "/tmp/wsproxy.$(pid)"

  # Configure the SSL options for the server.
  ssl:
    # If true, SSL is enabled, otherwise no.
    enabled: true

    # Certificate options.
    cert_path: "/path/to/cert.pem"
    key_path: "/path/to/cert.key"

# Client Configuration
#
# Configuration options when running as a client (when this wsproxy
# instance will attempt to connect to another wsproxy server). Unlike
# the server options, multiple clients can be configured.
clients:
    # First client
 -  url: "wss://wsproxyserver.com"
    enabled: true
    ssl:
      enabled: true
      cert_path: "/path/to/verify/cert"
      # Set to true to include verifying the host as a part of the
      # validation.
      verify_host: false
    # Second client (if desired), etc.
"""
