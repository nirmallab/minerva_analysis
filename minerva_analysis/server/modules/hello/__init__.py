"""Throwaway module proving the Phase 1-3 extension seam actually works end to
end (see Phase 4 of the modularization plan). Not wired into any shipped
build -- MINERVA_ACTIVE_MODULE defaults to "gating" everywhere. Safe to
delete once a real second module (e.g. roi) exists; a real module author
should copy this package's shape (register(app) + one Blueprint) rather than
build on top of it.
"""

from minerva_analysis.server.modules.hello.routes import hello_bp


def register(app):
    app.register_blueprint(hello_bp)
