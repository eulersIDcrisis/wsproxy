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
# Superuser authentication parameters here. These parameters identify
# this wsproxy instance to other instances.
auth:
  type: basic
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

  # Configure the SSL options for the server.
  ssl:
    # If true, SSL is enabled, otherwise no.
    enabled: true

    # Certificate options.
    cert_path: "/path/to/cert.pem"
    key_path: "/path/to/cert.key"

  # Configure access for different clients.
  #
  # NOTE: The superuser credentials can be used to authenticate access
  # for other users.
  clients:
   - auth:
     type: basic
     username: admin
     password: password

# Client Configuration
#
# Configuration options when running as a client (when this wsproxy
# instance will attempt to connect to another wsproxy server). Unlike
# the server options, multiple clients can be configured.
clients:
    # First client
 -  url: "wss://wsproxyserver.com"
    enabled: true
    auth:
      type: basic
      username: client
      password: password2

    # Configure whether to use a custom certificate chain for this
    # client's connection.
    ssl:
      enabled: true
      cert_path: "/path/to/verify/cert"
      # Set to true to include verifying the host as a part of the
      # validation.
      verify_host: false
    # Second client (if desired), etc.
"""
