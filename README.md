# wsproxy

A Python tornado-based WS proxy

This project is my hobbyist project for tunneling between LANs.
Normally, the solution here is to use a VPN to bridge two different
networks, but sometimes configuring the router for both LANs is
prohibitively difficult.

Another solution is SSH tunnelling; the configuration for SSH is
usually simpler than some VPNs. However, this solution requires
knowing the public IP Address (brittle!) or setting up a DNS
domain for both networks.

SSH and VPN are fine solutions that should be used and considered
for most cases. But for the fun of it, I created this project to
permit tunneling using a standard HTTP/WS connection instead of
requiring VPN or SSH. This likely isn't as efficient, but because
it is over HTTP/WS and has the potential for a few more features
that aren't a standard part of VPN.

## Using Wsproxy

Using the proxy requires
