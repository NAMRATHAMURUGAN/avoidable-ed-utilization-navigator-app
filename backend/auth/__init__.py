"""Session-based authentication/authorization enforcement.

Deliberately isolated from backend/safety/ so authentication/authorization
logic can never be confused with, or influence, the deterministic clinical
safety engine. Decorators in this package only gate access to application
functionality; they never call or alter backend/safety/engine.py.
"""
