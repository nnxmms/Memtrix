#!/usr/bin/python3

"""SSH error types surfaced to the agent."""


class SSHError(Exception):
    """Raised for SSH connection or command failures surfaced to the agent."""


class SSHTimeout(SSHError):
    """Raised when a remote command exceeds the configured timeout."""
