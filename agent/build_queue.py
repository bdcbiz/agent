"""NODE4 builds wait for an explicitly bounded, managed worker; never default."""
import socket


def remote_build_queue():
    return 'bounded-build' if socket.gethostname() == 'NODE4' else 'default'
